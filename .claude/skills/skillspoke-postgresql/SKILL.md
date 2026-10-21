---
name: skillspoke-postgresql
description: >-
  SkillSpoke's decisions about PostgreSQL: where they are in the arc42 architecture and the
  values a SkillSpoke cluster, data-access layer and schema use. Load it with the
  agent-teams-workforce `aurora-postgresql` skill whenever you design, specify, review,
  implement, test or provision anything that stores SkillSpoke data in PostgreSQL.
user-invocable: false
---

# PostgreSQL in SkillSpoke

The generic guide is the `aurora-postgresql` skill. The source of every SkillSpoke decision is
the effective arc42 architecture in the `skillspoke-docs` vault, under
`docs/tech/architecture/arc42/`; read the view that covers your task before you decide anything.

| View | Holds |
|---|---|
| `08-crosscutting-concepts/persistence-per-data-set.md` | Which data set lives in PostgreSQL, its owning service, and the PostgreSQL topology (one cluster per owning service, Data API only, why Aurora) |
| `07-deployment-view/environments-and-accounts.md` | Capacity, availability, backups and deletion protection per environment |
| `07-deployment-view/network.md` | The `rds-data` interface endpoint and the security groups |
| `05-building-block-view/shared-components.md` | `PlatformPostgresCluster` in `shared-cdk-lib`; the Data API client, outbox writer and `OutboxRelay` in `skillSpoke-dataAccess-lib` |
| `08-crosscutting-concepts/powertools-and-chassis.md` | What the chassis and `skillSpoke-dataAccess-lib` give every PostgreSQL handler |
| `08-crosscutting-concepts/concurrency-control.md` | Contended writes and atomic unique creation |
| `08-crosscutting-concepts/migration-and-backfill.md` | How data migrations and backfills run |
| `08-crosscutting-concepts/service-isolation.md` | Store ownership; no service reads another service's database |

## SkillSpoke values

- **Cluster.** Aurora PostgreSQL Serverless v2, one cluster per owning service in that service's
  own stack, built with `PlatformPostgresCluster` from `shared-cdk-lib`. Use the latest major
  version `PlatformPostgresCluster` supports.
- **Capacity.** In `dev` the minimum capacity is 0 ACU with automatic pause. In `qa` and `prod`
  the minimum is 0.5 ACU with a reader in a second AZ. These change only through
  `PlatformPostgresCluster` settings in CDK.
- **Data access.** The Data API client, its retry while a paused cluster resumes
  (`DatabaseResumingException`), the transactional outbox writer and the `OutboxRelay` handler
  are in `skillSpoke-dataAccess-lib`. The cluster ARN, secret ARN and database name come from
  SSM Parameter Store parameters the owning stack writes, read through the chassis.
- **Events.** A state change that announces an event writes its outbox row in the same
  transaction, through the library's outbox writer (`skillspoke-events` skill, "Publish after
  commit").
- **Extensions in use.** `vector` (pgvector 0.8.x), `pg_trgm`, and PostGIS where a service
  needs it.
- **Temporal facts.** The company and normalized datasets are effective-dated fact models.
- **Multi-tenancy.** Tables shared by many job seekers carry the seeker's id, and every query
  filters on it.
- **GraphRAG.** The graph-database case includes following the entities and relationships
  extracted from a seeker's imported documents (resumes, CVs and other documents).
