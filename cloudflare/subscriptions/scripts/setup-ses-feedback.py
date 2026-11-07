#!/usr/bin/env python3
"""Provision a dedicated SES event destination; never changes Worker delivery mode.

Requires AWS CLI v2 and credentials in its standard provider chain. The
provision and subscribe phases are intentionally separate: deploy the Worker
with the exact topic first, so SNS confirmation cannot be lost.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

REGION = 'ap-southeast-1'
TOPIC = 'gids-subscription-feedback'
CONFIG = 'gids-subscription-feedback'
DESTINATION = 'gids-sns-feedback'
ENDPOINT = 'https://subscriptions.globalinfectiousdisease.com/api/internal/ses-sns-feedback'
EVENTS = ['BOUNCE', 'COMPLAINT', 'DELIVERY', 'DELIVERY_DELAY']


def aws(*args):
    result = subprocess.run(['aws', *args, '--region', REGION, '--output', 'json', '--no-cli-pager'],
                            text=True, capture_output=True, env={**os.environ, 'AWS_PAGER': ''})
    if result.returncode:
        # Avoid echoing command lines, raw response bodies or credentials.
        raise RuntimeError('AWS command failed: ' + ' '.join(args[:2]) + '. Verify credentials, region and IAM permissions.')
    return json.loads(result.stdout or '{}')


def identities():
    who = aws('sts', 'get-caller-identity')
    account = str(who.get('Account', ''))
    arn = str(who.get('Arn', ''))
    if not re.fullmatch(r'\d{12}', account) or not arn.startswith('arn:aws:'):
        raise RuntimeError('Expected an AWS commercial account with a 12-digit account ID.')
    return account, f'arn:aws:sns:{REGION}:{account}:{TOPIC}', f'arn:aws:ses:{REGION}:{account}:configuration-set/{CONFIG}'


def topic_policy(account, topic, config_arn):
    return {'Version': '2012-10-17', 'Statement': [
        {'Sid': 'AccountManagement', 'Effect': 'Allow', 'Principal': {'AWS': f'arn:aws:iam::{account}:root'},
         'Action': 'SNS:*', 'Resource': topic},
        {'Sid': 'SesConfigurationSetPublish', 'Effect': 'Allow', 'Principal': {'Service': 'ses.amazonaws.com'},
         'Action': 'SNS:Publish', 'Resource': topic,
         'Condition': {'StringEquals': {'AWS:SourceAccount': account, 'AWS:SourceArn': config_arn}}},
    ]}


def desired_destination(topic):
    return {'Enabled': True, 'MatchingEventTypes': EVENTS, 'SnsDestination': {'TopicArn': topic}}


def provision():
    account, topic, config_arn = identities()
    policy = topic_policy(account, topic, config_arn)
    existing = {x['TopicArn'] for x in aws('sns', 'list-topics').get('Topics', [])}
    if topic not in existing:
        # Policy is supplied at creation, never overwrite a pre-existing policy.
        attrs = json.dumps({'Policy': json.dumps(policy, separators=(',', ':'))}, separators=(',', ':'))
        created = aws('sns', 'create-topic', '--name', TOPIC, '--attributes', attrs)
        if created.get('TopicArn') != topic:
            raise RuntimeError('Unexpected topic ARN; stopped.')
    attrs = aws('sns', 'get-topic-attributes', '--topic-arn', topic).get('Attributes', {})
    try:
        current = json.loads(attrs['Policy'])
    except (KeyError, ValueError) as exc:
        raise RuntimeError('Cannot verify topic policy; stopped.') from exc
    if attrs.get('Owner') != account or current.get('Statement') != policy['Statement']:
        raise RuntimeError('Existing topic policy differs from the dedicated policy; no changes made to that policy.')
    subs = aws('sns', 'list-subscriptions-by-topic', '--topic-arn', topic).get('Subscriptions', [])
    if any(s.get('Protocol') != 'https' or s.get('Endpoint') != ENDPOINT for s in subs):
        raise RuntimeError('Topic has another subscriber; refusing to use it for private delivery events.')
    if attrs.get('SignatureVersion') != '2':
        aws('sns', 'set-topic-attributes', '--topic-arn', topic, '--attribute-name', 'SignatureVersion', '--attribute-value', '2')
    config_sets = aws('sesv2', 'list-configuration-sets').get('ConfigurationSets', [])
    if CONFIG not in config_sets:
        aws('sesv2', 'create-configuration-set', '--configuration-set-name', CONFIG)
    destinations = aws('sesv2', 'get-configuration-set-event-destinations', '--configuration-set-name', CONFIG).get('EventDestinations', [])
    if any(d.get('Name') != DESTINATION for d in destinations):
        raise RuntimeError('Configuration set has other destinations; refusing to reuse it.')
    matched = [d for d in destinations if d.get('Name') == DESTINATION]
    desired = desired_destination(topic)
    if not matched:
        aws('sesv2', 'create-configuration-set-event-destination', '--configuration-set-name', CONFIG,
            '--event-destination-name', DESTINATION, '--event-destination', json.dumps(desired, separators=(',', ':')))
    else:
        actual = matched[0]
        if (len(matched) != 1 or actual.get('Enabled') is not True
            or set(actual.get('MatchingEventTypes', [])) != set(EVENTS)
            or actual.get('SnsDestination', {}).get('TopicArn') != topic
            or any(actual.get(k) for k in ('KinesisFirehoseDestination', 'CloudWatchDestination', 'EventBridgeDestination', 'PinpointDestination'))):
            raise RuntimeError('Existing SES event destination differs; refusing to overwrite it.')
    print('AWS resources prepared. Configure and deploy the Worker before subscribing:')
    print('SUBSCRIPTIONS__SES_SNS_FEEDBACK_ENABLED=true')
    print('SUBSCRIPTIONS__SES_SNS_TOPIC_ARN=' + topic)
    print('Keep SUBSCRIPTIONS__SMTP_SES_CONFIGURATION_SET empty and SUBSCRIPTIONS__DELIVERY_MODE=pilot.')
    print('Then run this script with subscribe. After confirmation, set SUBSCRIPTIONS__SMTP_SES_CONFIGURATION_SET=' + CONFIG)


def check_endpoint():
    # Unsigned empty input must reach the enabled adapter and be rejected.
    request = urllib.request.Request(ENDPOINT, data=b'{}', method='POST',
                                     headers={'Content-Type': 'text/plain; charset=UTF-8',
                                              'User-Agent': 'Amazon Simple Notification Service Agent'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    if status != 400:
        raise RuntimeError(f'Feedback endpoint returned {status}, not 400 for an unsigned probe; deploy/inspect firewall before subscribing.')


def subscribe():
    account, topic, config_arn = identities()
    attrs = aws('sns', 'get-topic-attributes', '--topic-arn', topic).get('Attributes', {})
    try:
        policy = json.loads(attrs['Policy'])
    except (KeyError, ValueError) as exc:
        raise RuntimeError('Cannot verify topic policy; stopped.') from exc
    if (attrs.get('Owner') != account or attrs.get('SignatureVersion') != '2'
        or policy.get('Statement') != topic_policy(account, topic, config_arn)['Statement']):
        raise RuntimeError('Topic security settings differ; run provision and inspect before subscribing.')
    check_endpoint()
    subs = aws('sns', 'list-subscriptions-by-topic', '--topic-arn', topic).get('Subscriptions', [])
    if any(s.get('Protocol') != 'https' or s.get('Endpoint') != ENDPOINT for s in subs):
        raise RuntimeError('Topic has another subscriber; refusing to use it for private delivery events.')
    matches = [s for s in subs if s.get('Protocol') == 'https' and s.get('Endpoint') == ENDPOINT]
    if len(matches) > 1:
        raise RuntimeError('Multiple matching SNS subscriptions found; inspect before proceeding.')
    if not matches:
        aws('sns', 'subscribe', '--topic-arn', topic, '--protocol', 'https', '--notification-endpoint', ENDPOINT)
        print('Subscription requested. Verify SNS confirmation and a real delivery receipt; no email was sent by this script.')
    else:
        state = matches[0].get('SubscriptionArn', '')
        print('Existing subscription:', 'pending confirmation' if state == 'PendingConfirmation' else 'confirmed' if state.startswith(topic + ':') else 'unknown')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['provision', 'subscribe'])
    args = parser.parse_args()
    if not shutil.which('aws'):
        raise RuntimeError('AWS CLI v2 is required; configure credentials locally without sharing secrets.')
    (provision if args.action == 'provision' else subscribe)()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
