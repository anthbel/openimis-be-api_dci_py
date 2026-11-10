# DCI Subscription Notification System

SPDCI-compliant notification system for registry change events.

## Architecture

The notification system follows the SPDCI FR (Farmer Registry) specification for subscribe/unsubscribe/notify operations.

### Components

```
┌─────────────────────────────────────────────────────────────┐
│                     Individual Model                         │
│              (Create/Update/Delete events)                   │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   Django Service Signals                     │
│            (individual_service.create_or_update)             │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│               DCISubscriptionFilter                          │
│   (Find matching active subscriptions by event_type)        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│            DCINotificationManager                            │
│     (Build SPDCI notify payload for each subscriber)        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│             DCINotificationClient                            │
│    (Send async HTTP POST to subscriber endpoints)           │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              DCINotificationLog                              │
│         (Record success/failure in database)                 │
└─────────────────────────────────────────────────────────────┘
```

## SPDCI Notify Format

When an Individual is created/updated/deleted, subscribers receive a POST request:

```json
POST {subscriber.sender_uri}
Content-Type: application/json

{
  "signature": "",
  "header": {
    "version": "1.0.0",
    "message_id": "msg-notify-abc123",
    "message_ts": "2026-03-17T10:00:00Z",
    "action": "notify",
    "sender_id": "openimis",
    "receiver_id": "subscriber-system-id",
    "is_msg_encrypted": false
  },
  "message": {
    "transaction_id": "txn-notify-xyz789",
    "correlation_id": "original-subscription-txn-id",
    "notify_request": [
      {
        "reference_id": "ref-abc123",
        "timestamp": "2026-03-17T10:00:00Z",
        "subscription_id": "sub-16hexchars",
        "data": {
          "version": "1.0.0",
          "reg_type": "FR",
          "reg_record_type": "Person",
          "event_type": "REGISTER",
          "reg_record": {
            "id": "uuid",
            "first_name": "John",
            "last_name": "Doe",
            ...
          }
        }
      }
    ]
  }
}
```

## Event Types

- **REGISTER**: New Individual created
- **UPDATE**: Existing Individual updated
- **DEREGISTER**: Individual deleted/deactivated
- **ALL**: Subscription receives all event types

## Configuration

Control notification behavior via environment variables:

```bash
# Enable/disable Individual notifications (default: False)
# When True, every Individual create, update and delete sends the full
# Person record to every active subscription whose event type and filter match.
DCI_SUBSCRIBE_INDIVIDUAL_SIGNAL=False

# HTTP timeout for notifications in seconds (default: 30)
DCI_NOTIFICATION_TIMEOUT=30

# Registry ID in notification headers (default: openimis)
DCI_REGISTRY_ID=openimis

# Registry type: FR (Farmer), SR (Social), IBR (Identity)
SPDCI_REGISTRY_TYPE=FR
```

## Subscription Workflow

1. **Subscribe**: External system calls `POST /api/api_dci/registry/subscribe`
   - Receives ACK response immediately
   - Subscription stored in `tblDCISubscription`
   - Must provide `sender_uri` for callbacks

2. **Event occurs**: Individual is created/updated/deleted
   - Django service signal fired
   - `DCISubscriptionFilter` finds matching subscriptions
   - `DCINotificationManager` builds SPDCI notify payload
   - `DCINotificationClient` sends async HTTP POST to each subscriber

3. **Logging**: All notification attempts logged in `tblDCINotificationLog`
   - Success/failure status
   - HTTP status code
   - Error message if failed
   - Correlation ID for tracking

4. **Unsubscribe**: External system calls `POST /api/api_dci/registry/unsubscribe`
   - Subscription status changed to INACTIVE
   - No more notifications sent

## Filter Criteria (Future Enhancement)

Subscriptions can include optional `filter_criteria` to receive only matching events:

```json
{
  "filter": {
    "idtype-value": {
      "id_type": "NATIONAL_ID",
      "id_value": "123456"
    }
  }
}
```

Currently, filter evaluation is basic. Full implementation planned for:
- `expression`: JSONPath-like expressions
- `predicate`: Complex filter predicates

## Database Tables

### tblDCISubscription
Stores active and inactive subscriptions with:
- `subscription_code`: Unique identifier
- `sender_id`: Subscriber system ID
- `sender_uri`: Callback URL
- `event_type`: REGISTER/UPDATE/DEREGISTER/ALL
- `filter_criteria`: Optional JSON filter
- `status`: ACTIVE/INACTIVE/SUSPENDED
- `expiring`: Optional expiration date

### tblDCINotificationLog
Audit log of all notification attempts:
- `subscription`: Foreign key to subscription
- `event_type`: Event that triggered notification
- `resource_type`: Person/Farmer/Member
- `resource_id`: ID of changed resource
- `notified_successfully`: Boolean success flag
- `http_status_code`: HTTP response code
- `error`: Error message if failed
- `correlation_id`: Tracking identifier

## Error Handling

- **No sender_uri**: Notification skipped, logged as failure
- **HTTP 4xx/5xx**: Logged as failure with status code
- **Timeout**: Logged as failure with timeout message
- **Network error**: Logged as failure with exception
- **Invalid headers**: Logged as failure

Notifications are fire-and-forget with no automatic retry. Subscribers should implement idempotency and handle duplicate notifications.

## Testing

See test files in `tests/` directory for:
- Unit tests for filter logic
- Integration tests for notification flow
- Mock subscriber endpoints

## Security

- Subscriptions require authentication (DCIPersonPermissions)
- Optional callback_headers for custom auth (encrypted in DB)
- HTTPS recommended for sender_uri endpoints
- Signature field reserved for future message signing
