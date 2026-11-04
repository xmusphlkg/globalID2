"""Safety checks for the opt-in SES/SNS provisioning script (no AWS calls)."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

PATH = Path(__file__).resolve().parents[2] / 'cloudflare/subscriptions/scripts/setup-ses-feedback.py'
spec = importlib.util.spec_from_file_location('setup_ses_feedback', PATH)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

ACCOUNT = '123456789012'
TOPIC = f'arn:aws:sns:{setup.REGION}:{ACCOUNT}:{setup.TOPIC}'
CONFIG = f'arn:aws:ses:{setup.REGION}:{ACCOUNT}:configuration-set/{setup.CONFIG}'


def stateful_aws(*, extra_subscriber=False, changed_policy=False, changed_destination=False):
    calls = []
    policy = setup.topic_policy(ACCOUNT, TOPIC, CONFIG)
    if changed_policy:
        policy['Statement'].append({'Effect': 'Allow', 'Principal': '*', 'Action': 'SNS:Publish', 'Resource': TOPIC})
    destination = {'Name': setup.DESTINATION, **setup.desired_destination(TOPIC)}
    if changed_destination:
        destination['SnsDestination'] = {'TopicArn': 'wrong'}
    def fake(*args):
        calls.append(args)
        if args[:2] == ('sts', 'get-caller-identity'):
            return {'Account': ACCOUNT, 'Arn': f'arn:aws:iam::{ACCOUNT}:user/test'}
        if args[:2] == ('sns', 'list-topics'):
            return {'Topics': [{'TopicArn': TOPIC}]}
        if args[:2] == ('sns', 'get-topic-attributes'):
            import json
            return {'Attributes': {'Owner': ACCOUNT, 'SignatureVersion': '2', 'Policy': json.dumps(policy)}}
        if args[:2] == ('sns', 'list-subscriptions-by-topic'):
            return {'Subscriptions': ([{'Protocol': 'email', 'Endpoint': 'elsewhere@example.org'}] if extra_subscriber else [])}
        if args[:2] == ('sesv2', 'list-configuration-sets'):
            return {'ConfigurationSets': [setup.CONFIG]}
        if args[:2] == ('sesv2', 'get-configuration-set-event-destinations'):
            return {'EventDestinations': [destination]}
        return {}
    return calls, fake


def test_policy_restricts_ses_source_and_account():
    policy = setup.topic_policy(ACCOUNT, TOPIC, CONFIG)
    stmt = policy['Statement'][1]
    assert stmt['Principal'] == {'Service': 'ses.amazonaws.com'}
    assert stmt['Condition']['StringEquals'] == {'AWS:SourceAccount': ACCOUNT, 'AWS:SourceArn': CONFIG}
    assert set(setup.desired_destination(TOPIC)['MatchingEventTypes']) == {'BOUNCE', 'COMPLAINT', 'DELIVERY', 'DELIVERY_DELAY'}


def test_existing_matching_resources_are_not_modified():
    calls, fake = stateful_aws()
    with patch.object(setup, 'aws', fake):
        setup.provision()
    assert not [c for c in calls if c[1].startswith(('create-', 'set-', 'update-', 'delete-'))]


@pytest.mark.parametrize('opts', [{'changed_policy': True}, {'extra_subscriber': True}, {'changed_destination': True}])
def test_conflicts_fail_closed_without_overwriting(opts):
    calls, fake = stateful_aws(**opts)
    with patch.object(setup, 'aws', fake), pytest.raises(RuntimeError):
        setup.provision()
    assert not [c for c in calls if c[1].startswith(('create-', 'set-', 'update-', 'delete-'))]


def test_subscription_rejects_other_destinations():
    calls, fake = stateful_aws(extra_subscriber=True)
    with patch.object(setup, 'aws', fake), patch.object(setup, 'check_endpoint'), pytest.raises(RuntimeError):
        setup.subscribe()
    assert not [c for c in calls if c[:2] == ('sns', 'subscribe')]


@pytest.mark.parametrize(('status', 'allowed'), [(400, True), (404, False), (403, False)])
def test_unsigned_probe_requires_enabled_endpoint(status, allowed):
    import urllib.error
    failure = urllib.error.HTTPError(setup.ENDPOINT, status, 'expected probe', {}, None)
    with patch.object(setup.urllib.request, 'urlopen', side_effect=failure):
        if allowed:
            setup.check_endpoint()
        else:
            with pytest.raises(RuntimeError):
                setup.check_endpoint()
