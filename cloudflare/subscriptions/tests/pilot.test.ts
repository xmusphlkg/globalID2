import assert from 'node:assert/strict';
import test from 'node:test';
import { deliveryMode, recipientAllowed } from '../src/lib/pilot.ts';
import { smtpConfig } from '../src/lib/smtp-config.ts';

test('pilot requires the exact configured administrator and fails closed on configuration errors', () => {
  assert.equal(deliveryMode({}), 'pilot');
  assert.equal(deliveryMode({ DELIVERY_MODE: 'FULL' }), 'pilot');
  assert.equal(recipientAllowed({}, 'reader@example.test'), false);
  assert.equal(recipientAllowed({ PILOT_RECIPIENTS: ' ADMIN@example.test ' }, 'admin@example.test'), true);
  assert.equal(recipientAllowed({ PILOT_RECIPIENTS: 'admin@example.test' }, 'other@example.test'), false);
  assert.equal(recipientAllowed({ DELIVERY_MODE: 'full' }, 'other@example.test'), true);
  const config = smtpConfig({ SMTP_HOST: 'smtp.test', SMTP_USERNAME: 'user', SMTP_PASSWORD: 'secret', SMTP_FROM_EMAIL: 'a@example.test', PILOT_RECIPIENTS: 'admin@example.test' });
  assert.equal(config?.pilotRecipients, 'admin@example.test');
});
