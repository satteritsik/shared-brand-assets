---
name: skillspoke-events
description: >-
  Use whenever you design, specify, review or implement anything that produces, routes or
  receives a SkillSpoke event: the standard event, event type names, the path an event takes
  from its producer through the central event API to the bus and from routing rules to their
  targets, and what a producer and a target owner must do.
user-invocable: false
---

# SkillSpoke events

The source is the effective arc42 architecture in the `skillspoke-docs` vault, under
`docs/tech/architecture/arc42/`:

| View | Holds |
|---|---|
| `08-crosscutting-concepts/event-platform-and-envelope.md` | The standard event and its fields, the four categories, the central event API and the events API function, the event type registry, coat-check, routing rules and their targets, each target's retry policy and DLQ |
| `08-crosscutting-concepts/event-consumption.md` | What a target does after a rule delivers an event to it: duplicate suppression, reading a reference payload, applying only newer versions, the target owner's failure handling, the configuration of an SQS target |
| `08-crosscutting-concepts/event-naming-standard.md` | Event type names |
| `08-crosscutting-concepts/events-as-observability.md` | The kinds of event every service emits, lifecycle pairs, what an error event carries, the event log, correlation |
| `08-crosscutting-concepts/idempotency.md` | Duplicate suppression in the chassis |
| `08-crosscutting-concepts/retry-and-backoff.md` | The one retry policy and the owner of each retry |
| `05-building-block-view/events-platform/README.md` | What `shared-events-service` and `shared-events-infra` own |
| `06-runtime-view/event-publication-and-delivery.md` | The path every event takes |
| `06-runtime-view/pii-present-event-publication.md` | The publish sequence of an event that carries personal data |
| `06-runtime-view/event-catalog.md` | Event types with their producers and description |

Read the view that covers your task before you decide anything this skill does not state.

## The standard event

Every event is the standard event `SkillSpokeEvent`. Its fields, which are required, and the
three identifiers (`eventId`, `trace.correlationId`, the dedup key) are in The standard event
and Design of `event-platform-and-envelope.md`.

- The contract is the EventBridge schema registry entry: registry `skillspoke-events`, schema
  `skillspoke@standard-event`, region `us-east-1`
  (`arn:aws:schemas:us-east-1:616930583457:schema/skillspoke-events/skillspoke@standard-event`).
  [`standard-event.schema.json`](standard-event.schema.json), beside this file, is a reading copy;
  the registry content governs.
- `category` is one of the four categories `user`, `domain`, `data` and `system`, defined in
  `event-platform-and-envelope.md`. The producer chooses the category of each event at run time.
- Lifecycle pairs and what an error or warning event carries are in
  `events-as-observability.md`.

## Event type names

Name every event type to `event-naming-standard.md`, and check the name against it before the
type is registered. Examples: `identity.user.registered`, `billing.invoice.finalized`,
`job-search.results.published`.

## Ownership

| Owner | Owns |
|---|---|
| `shared-events-infra` | The bus `skillSpoke-events`, its resource policy, the all-events rule and the event log group `/aws/events/skillSpoke-events` it targets, the bus SSM parameters |
| `shared-events-service` | The central event API and the events API function (`events-gateway`) behind it, building and validating the standard event, publication to the bus, coat-check, the registry path scheme, the `visibility` vocabulary |
| Each producer | The registry entries of the event types it publishes, their payload schemas and payload models, its own OAuth2 app client |
| Stack that owns a rule's target(s) | The routing rule, its targets, each target's retry policy and DLQ and DLQ-depth alarm, its grant on the coat-check prefixes it reads |
| `shared-chassis-lib` | The event helper, consumer batch handling, duplicate suppression, the mapping of failures to named errors |
| `shared-apiClient-lib` | The OAuth2 token, the HTTP call and its retries |

- An event type may be published by any number of producers.
- Because an aggregate has authority only over its own facts, it emits only facts it owns, even
  when another aggregate lives in the same service.
- Because a browser holds no service app client and has no path to the private API, a fact about a
  browser interaction is published by the backend context that owns it.

## The path an event takes

```text
producer -> chassis event helper -> shared-apiClient-lib -> POST /v1/events (central event API)
  -> the events API function builds, validates, publishes -> bus skillSpoke-events
  -> every routing rule whose event pattern matches -> that rule's targets
```

- A producer builds the request body for one event and calls the central event API. It knows
  nothing about rules or targets.
- `shared-events-service` is the only builder, validator and publisher of events. Any code can
  send an event; it sends the values of the event and an optional payload in the body of its API
  request, which is not the event, as many times as it needs in one execution.
- Because the bus resource policy grants `events:PutEvents` to the events API function's role
  only, the central event API is the one way onto the bus for every SkillSpoke principal.
- Because every service-to-service call identifies its caller through its own app client, and a
  public endpoint would carry PII-bearing events off the private network, the central event API is
  a PRIVATE REST v1 API (`POST /v1/events`) reachable only through the platform `execute-api`
  interface endpoint and authorized by a Cognito user-pool authorizer that checks the
  event-publish scope of a client-credentials token.
- All cross-service events flow through one custom bus, `skillSpoke-events`.
- The all-events rule routes every event to the event log, the CloudWatch Logs log group
  `/aws/events/skillSpoke-events` owned by the events service, asynchronously after publication.
  The event log is where a request is investigated; the durable record of an employee action is
  the employee audit trail.
- The event platform ends where a rule forwards an event to its target. The target and the
  processing behind it belong to the target's owner (`event-consumption.md`).

## What a producer does

1. **Register the event type.** Because the registry is closed-world, the producer's own stack
   writes one SSM parameter per event type before first publication, at
   `/skillSpoke/{serviceName}/eventRegistry/{seg1}/{seg2}/{seg3}[/{seg4}]` (each dot segment of the
   event type as one camelCase path segment), with the value `{piiPresent}`. Because the entry
   lives in the producer's stack, a new event type needs no deployment of
   `shared-events-service`.
2. **Classify the payload model.** Because the registry's `piiPresent` is derived at synth from
   the payload model, every field of the model carries a PII classification and an unclassified
   field counts as PII.
3. **Publish after commit.** Because an event must exist if and only if its state change durably
   committed, a producer emits from a transactional outbox, from a change-data-capture stream on
   its own store (a DynamoDB stream feeding a publisher Lambda), or from a queue-driven handler
   after its own conditional write. Because a rejected or failed publication must be retried,
   the fact stays in that outbox, stream or DLQ until it is accepted. A change-data-capture
   publisher's event-source mapping declares an on-failure destination, bisect-on-function-error,
   a maximum retry count and a maximum record age, and carries an iterator-age alarm, because a
   poison record otherwise blocks every later change on its shard unseen.
4. **Fill in the API request with the chassis event helper.** The helper provides static helpers
   and data structures that the sending code fills in, including its OAuth2 client id and secret.
   They build the body of the API request, which is not the event. Its one definition is the
   request schema of `POST /v1/events`, `PublishEventRequest`, which the `shared-events-service`
   repository owns and loads at run time from `request_schema.json` in its events package; the
   `trace` identifiers of the current work come from
   `RequestContext`. Code that runs outside the chassis calls `shared-apiClient-lib` itself with
   the same request body. The request names no rule and no target.
5. **Pass a dedup key.** Because delivery is at-least-once and `eventId` differs on every
   publication, the producer derives a dedup key from the fact itself (the entity, the transition
   and the entity's version or sequence) and passes the identical key on every republication.
   Because coat-check keys and access logs carry it verbatim, the key is an opaque token; a key
   derived from an identifier is a keyed hash. Fan-out to several channels keeps one key.
6. **Pass the subject account id** with an event whose payload carries personal data, because the
   coat-check key is `<eventType>/<subjectAccountId>/<dedupKey>` and forget-me finds an account's
   payloads by that prefix.

### What the events service decides for the producer

- Because a caller could otherwise claim another service's identity, `source.service` and `actor`
  come from the caller's verified OAuth2 client identity: the events API function maps the
  token's `client_id` to the calling service and sets `source.service` to it. The actor is the
  calling service, of type `system`; a user subject travels as payload data.
- Because `eventId` and `timestamps.publishedAt` describe the publication, the events API function
  always generates them.
- Because classification must hold even when a payload is opaque, the events API function
  classifies from the event's own fields and the registry entry, and never reads or redacts
  `payload.inline`.
- Because a state change must not be reported before it is durable, the events API function
  returns 202 only after `PutEvents` returns `FailedEntryCount` 0.
- Idempotency of the publish endpoint runs inside the events API function, on its own DynamoDB
  idempotency table; when that table cannot be reached, the API returns 500 and publishes nothing.

| Response | When | What the chassis event helper does |
|---|---|---|
| 202 (`eventId`, `status` `accepted`, `event_type`, `domain`, `event_bus`) | The event is on the bus | Continues |
| 400 | The body is unparseable | Permanent failure |
| 422 with `details[]` naming each failed field | The event fails validation: an unknown field, a missing required value, a `category` that is not one of the four, a `trace` without `trace.correlationId`, an absent or inconsistent `visibility` block (`piiPresent` true with `public`), an unregistered event type, a `pii-required` type declared `piiPresent=false`, a `source.service` that disagrees with the caller's client identity or an unknown client, a malformed or identifying `dedupKey`, a producer-supplied reference with `piiPresent=true` | Raises `EnvelopeRejected`; because the request is a contract defect that a retry cannot fix, reports a batch item failure so the message goes to the producer's DLQ and is redriven after the contract is fixed |
| 403 from the API Gateway authorization layer | Infrastructure drift | Retries with bounded backoff, then dead-letters the message tagged `PublishAuthorisationFailure` |
| 503 | `PutEvents` or a coat-check write failed, or the registry could not be read on a cold cache | Transient: bounded retry with backoff |
| 500 | The idempotency table cannot be reached, or an unexpected fault in the events API function | Not retried; the fact stays in the producer's outbox, stream or DLQ for redrive |

Nothing is stored or published on any non-2xx response.

## Payloads

A payload is the domain-specific content of one event type; many events have none.

- Because a signal-only body makes a duplicate delivery benign and an out-of-order delivery
  self-correcting, an event that tells a target an entity changed carries the entity identifier
  and its new version, and not the entity's state.
- Because a target with destructive capability could act on a misread signal, a transition
  payload is reduced to the fact: the subject, the from-state, the to-state, the reason and the
  effective time.
- Because two competing events can arrive in the order that hides a failure, a workflow whose
  completion may be partial emits one completion event carrying per-item or per-field outcomes.
- Because an announced resource must be retrievable, a producer stores the resource durably
  before it publishes the event that announces it.
- Because the published event must fit the EventBridge and SQS size limits, the events API
  function moves an inline payload larger than 262,144 serialized UTF-8 bytes to the coat-check
  bucket and publishes a reference (`payload.mode` `reference`, with `storageType`, `location`,
  `checksum`, `encrypted`). Only the events API function offloads, inside its own invocation; API
  Gateway has no offload feature.
- Because no personal data may transit EventBridge, SQS, a DLQ or the event log, the events API
  function moves the payload of every `piiPresent=true` event to coat-check regardless of size.
- Because API Gateway invokes the events API function synchronously, a request is limited by the
  6 MB Lambda synchronous payload limit before API Gateway's 10 MB limit; the size limits are in
  the Coat-check pattern of `event-platform-and-envelope.md`. A producer whose `piiPresent=false`
  payload is too large for one request stores it itself and sends a reference.
- Because the coat-check key names the fact and the object is written put-if-absent, a
  republication of the same fact writes no new object.
- Because a coat-check object must outlive every queued reference to it and no longer, current
  objects expire after a configured window whose default is 14 days, the maximum SQS message
  retention.

## Schemas and versions

- The standard event has one schema, in the EventBridge schema registry (see The standard event).
- An event type with a payload may have a payload schema: the one schema authored for an event. Its
  version is the event's `eventSchemaVersion`, `payload.schemaUri` refers to it, and it may be
  registered in the EventBridge schema registry `skillspoke-events`. Registration is optional.
  Some payload schemas are standardized and shared across events (for example the description of
  an S3 object), so every target parses that shape the same way.
- Because a path or value must keep its meaning while a target still resolves it, a change to the
  standard event or to a payload schema is additive: no property removed, no new required
  property, no narrowed type, no removed enum value. A breaking change is a new version, and a
  breaking change to the standard event deploys in the same `deployment/waves.yaml` wave as
  `shared-events-service` and every producer it affects.
- Because the same value must decide the same way in every service, an enumeration that decides
  whether an event fires (a threshold set, a trigger-type vocabulary) is one central constant
  shared by every producer and rule, and each member binds to a registered event type.

## Routing

The routing rules are in Design of `event-platform-and-envelope.md`.

- A routing rule is its own element. The stack that owns a rule's targets declares and deploys
  the rule.
- A rule's event pattern matches one or more event types, or all events. It may match any field
  of the event, most often `eventType`, `domain`, `category` and the `lifecycle` fields; a rule
  that matches a payload field ties the rule to that payload's schema.
- A rule has up to five targets. A target is any EventBridge target type its owner chooses (for
  example an SQS queue, a Lambda function, a Step Functions state machine, an SNS topic or a
  CloudWatch Logs log group), in the bus's account and Region.
- An event matches zero, one or many rules. A publication succeeds whatever rules it matches.
- The alert dispatcher is never a target. A rule that matches `lifecycle.isError` is an ordinary
  routing rule, in the stack of the target it serves.
- An SQS target consumed by a Lambda has a visibility timeout of at least six times the function
  timeout, long polling, a declared retention, a redrive to its own DLQ after a `maxReceiveCount`
  of at least 5 and an alarm on that DLQ; the event-source mapping enables
  `ReportBatchItemFailures`. `ChassisFunction` derives the bounds.

## What a target owner does

Everything after a rule delivers an event to a target is the target owner's, as
`event-consumption.md` describes; this section and the next summarize it.

1. **Wait for the contract.** Because acting on an unregistered contract fabricates state, a
   target acts on a cross-domain event only once a producer's registry entry for it exists.
2. **Read every event as the standard event,** and validate a payload against the event type's
   payload schema.
3. **Suppress duplicates first.** Because EventBridge delivers at least once, every target that
   writes is idempotent: the chassis applies Powertools Idempotency on the target owner's own
   DynamoDB table, keyed on the dedup key, before the handler inspects `payload.mode`.
4. **Fail closed on the idempotency table.** Because treating an unreadable table as "no record"
   turns an outage into duplicated side effects, an unreachable table raises the retryable
   `IdempotencyStoreUnreachable`, reported as a failure of that record.
5. **Dereference a reference payload.** When `payload.mode` is `reference`, the target reads the
   object through the S3 gateway endpoint under its own `s3:GetObject*` grant on the
   `<eventType>/*` prefix. Because a payload may have expired or been forgotten, `NoSuchKey` maps
   to `ReferenceExpired` (permanent, acknowledged); `AccessDenied` maps to `ReferenceNotReachable`
   (alarmed); a connect timeout is transient.
6. **Report failures per record** when the target receives batches. Because one poisoned record
   would otherwise redrive the whole batch and re-apply every sibling, the handler reports batch
   item failures per record.
7. **Tolerate any order.** Because EventBridge may deliver out of order, a state-changing event
   carries a monotonic per-entity version in its payload, and the target stores the last-applied
   version and applies an event only when its version is strictly greater.
8. **Fail closed on cost and security.** Because a delayed re-enable must never overtake a
   suspension, a target whose projection gates cost or security moves to the less-secure state
   only on an event provably newer than the transition into the more-secure state.

## Retries and dead-letter handling

- Every retry uses jittered exponential backoff with a ceiling and a capped attempt count, and
  each kind of call has one owner: `shared-apiClient-lib` for the call to the central event API
  (connection errors, 429, 502, 503, 504), EventBridge under the target's retry policy for
  delivery to a target, and SQS redelivery for a failed handler behind an SQS target.
- Every target of a routing rule has its own retry policy and its own DLQ, a standard SQS queue in
  the same account and Region, declared with the rule in the stack that owns the target.
  EventBridge writes an event to that DLQ when delivery to that target fails after the retry
  policy is exhausted, or at once on a non-retryable error. The DLQ message is the event, with the
  attributes `RULE_ARN`, `TARGET_ARN`, `ERROR_CODE`, `ERROR_MESSAGE`,
  `EXHAUSTED_RETRY_CONDITION` and `RETRY_ATTEMPTS`. A CloudWatch alarm watches the DLQ's depth.
- After the cause is fixed, the target's owner redrives the DLQ to that one target only. An event
  is never republished to the bus. SkillSpoke uses no EventBridge archive and no replay.
- What a target does with an event after it has accepted it is the target owner's own failure
  handling (`event-consumption.md`).
- An idempotency record is retained longer than the retry horizon that can redeliver the same
  operation (the target's retry policy and, behind an SQS target, its redelivery sequence) plus
  margin; each operation type declares both lifetimes together.
- A scheduled reconciler re-applies DLQ and authoritative-store deltas to the target
  idempotently until a money, entitlement or audit projection converges, and alarms when it does
  not converge within its declared window.
- Recovery from a contract defect is to fix the registry entry or the producer, then redrive the
  producer's DLQ to the producer, or a target's DLQ to that one target.

## Correlation

- Because investigation starts from the correlation id, an event that carries `trace` carries
  `trace.correlationId`, and the same id travels on every hop of the request chain.
- Because the X-Ray header is lost in a DLQ, `trace.traceId` in the event is the durable link
  from an event to its trace.
