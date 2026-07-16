# OCI Operations Runbook

## Deployment Prerequisites

1. Confirm the existing Ubuntu 24.04 instance is ARM64 with at least 2 OCPU, 8 GB RAM, and 50 GB free disk; 4 OCPU and 16 GB or more is preferred.
2. Run `scripts/bootstrap-oci-vm.sh`, then attach and mount the Terraform-created Block Volume at `/srv/quant-trend-lab`.
3. Attach the Terraform output `application_nsg_ocid` to the existing primary VNIC. Do not add the existing instance or bucket to Terraform state.
4. Point a domain A record to the public IP. Production Caddy does not start without `PUBLIC_DOMAIN`.
5. Create the GitHub `production` environment and restrict it to `main`. Register the VM runner with labels `arm64` and `quant-trend-prod`; never run pull-request jobs on it.
6. When production deployment is ready, configure repository or organization variables `OCIR_REGISTRY` and `OCI_NAMESPACE`. Add repository or organization secrets for a dedicated CI user's `OCIR_USERNAME` and auth token, then set the repository or organization variable `OCI_DEPLOY_ENABLED` to `true`. Leave it unset otherwise; CI still builds both multi-architecture images without pushing them, and the deployment workflow stays disabled.
7. Load the PostgreSQL password, rate-limit HMAC secret, and read-only OCIR pull token into OCI Vault. Set their OCIDs and non-secret deployment values, then run `scripts/materialize-vault-secrets.sh`; it writes `/opt/quant-trend-lab/.env` with mode `0600` and logs in to OCIR without printing token values.
8. Run `scripts/install-systemd-units.sh` after the first release files are installed.

## Terraform Ownership

OCI Resource Manager owns the Terraform state. The stack reads the existing Compute instance and Object Storage bucket as data sources. It creates the dynamic group, IAM policy, NSG, Block Volume, weekly volume backup policy, two private OCIR repositories, Vault/key, notification topic, and CPU/memory alarms.

The runtime policy limits object access and `PAR_MANAGE` to the configured bucket. `PAR_MANAGE` is required separately from object read access so the API can create object-scoped download links that expire after 10 minutes.

The existing shared bucket lifecycle policy is intentionally not managed because replacing it could affect unrelated objects. `quant-trend-retention.timer` runs `scripts/cleanup-retention.sh` daily to delete `runs/` objects and database metadata older than seven days while retaining the seven newest PostgreSQL backup objects.

## Deployment and Rollback

The hosted GitHub runner tests the project and builds `linux/amd64` and `linux/arm64` images tagged with the Git SHA. When `OCI_DEPLOY_ENABLED` is `true`, it also pushes those images to OCIR and the production runner calls `scripts/deploy.sh` after CI succeeds on `main`. The script saves the previous image environment, applies Alembic, starts Compose, checks `https://<domain>/health/ready`, and restores the previous image tags on failure.

## Backup and Restore

- `quant-trend-backup.timer` runs `pg_dump` nightly and uploads to `backups/postgres/{timestamp}.dump` using instance-principal authentication.
- `quant-trend-retention.timer` removes expired run artifacts and retains seven daily database dumps.
- OCI backs up the Block Volume weekly with four-week retention.
- Run `scripts/restore-postgres.sh <object-name>` only during a maintenance window. Verify run metadata, artifact links, and `/health/ready` afterward.
- Perform a restore drill before the first public release and quarterly thereafter.
