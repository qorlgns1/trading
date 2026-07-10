data "oci_core_instance" "existing" {
  instance_id = var.instance_ocid
}

data "oci_objectstorage_bucket" "existing" {
  namespace = var.object_storage_namespace
  name      = var.bucket_name
}

resource "oci_identity_dynamic_group" "application" {
  compartment_id = var.tenancy_ocid
  name           = "quant-trend-lab-instance"
  description    = "Quant Trend Lab runtime instance principal"
  matching_rule  = "instance.id = '${var.instance_ocid}'"
  freeform_tags  = var.freeform_tags
}

resource "oci_identity_policy" "application" {
  compartment_id = var.compartment_ocid
  name           = "quant-trend-lab-runtime"
  description    = "Least-privilege runtime access for artifacts, backups, metrics, and secrets"
  statements = [
    "Allow dynamic-group ${oci_identity_dynamic_group.application.name} to manage objects in compartment id ${var.compartment_ocid} where target.bucket.name = '${var.bucket_name}'",
    "Allow dynamic-group ${oci_identity_dynamic_group.application.name} to read buckets in compartment id ${var.compartment_ocid} where target.bucket.name = '${var.bucket_name}'",
    "Allow dynamic-group ${oci_identity_dynamic_group.application.name} to manage buckets in compartment id ${var.compartment_ocid} where all {target.bucket.name = '${var.bucket_name}', request.permission = 'PAR_MANAGE'}",
    "Allow dynamic-group ${oci_identity_dynamic_group.application.name} to read secret-bundles in compartment id ${var.compartment_ocid}",
    "Allow dynamic-group ${oci_identity_dynamic_group.application.name} to use metrics in compartment id ${var.compartment_ocid}",
  ]
  freeform_tags = var.freeform_tags
}

resource "oci_core_network_security_group" "application" {
  compartment_id = var.compartment_ocid
  vcn_id         = var.vcn_ocid
  display_name   = "quant-trend-lab"
  freeform_tags  = var.freeform_tags
}

resource "oci_core_network_security_group_security_rule" "http" {
  network_security_group_id = oci_core_network_security_group.application.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = 80
      max = 80
    }
  }
}

resource "oci_core_network_security_group_security_rule" "https" {
  network_security_group_id = oci_core_network_security_group.application.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = 443
      max = 443
    }
  }
}

resource "oci_core_network_security_group_security_rule" "https_udp" {
  network_security_group_id = oci_core_network_security_group.application.id
  direction                 = "INGRESS"
  protocol                  = "17"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"
  udp_options {
    destination_port_range {
      min = 443
      max = 443
    }
  }
}

resource "oci_core_network_security_group_security_rule" "ssh" {
  network_security_group_id = oci_core_network_security_group.application.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = var.management_cidr
  source_type               = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }
}

resource "oci_core_network_security_group_security_rule" "egress" {
  network_security_group_id = oci_core_network_security_group.application.id
  direction                 = "EGRESS"
  protocol                  = "all"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
}

resource "oci_core_volume" "application" {
  availability_domain = data.oci_core_instance.existing.availability_domain
  compartment_id      = var.compartment_ocid
  display_name        = "quant-trend-lab-data"
  size_in_gbs         = var.block_volume_size_gb
  vpus_per_gb         = 10
  freeform_tags       = var.freeform_tags
  lifecycle { prevent_destroy = true }
}

resource "oci_core_volume_attachment" "application" {
  attachment_type = "paravirtualized"
  instance_id     = var.instance_ocid
  volume_id       = oci_core_volume.application.id
  display_name    = "quant-trend-lab-data"
}

resource "oci_core_volume_backup_policy" "weekly" {
  compartment_id = var.compartment_ocid
  display_name   = "quant-trend-lab-weekly-4"
  schedules {
    backup_type       = "INCREMENTAL"
    period            = "ONE_WEEK"
    retention_seconds = 2419200
    time_zone         = "REGIONAL_DATA_CENTER_TIME"
    hour_of_day       = 4
    day_of_week       = "SUNDAY"
  }
  freeform_tags = var.freeform_tags
}

resource "oci_core_volume_backup_policy_assignment" "application" {
  asset_id  = oci_core_volume.application.id
  policy_id = oci_core_volume_backup_policy.weekly.id
}

resource "oci_artifacts_container_repository" "application" {
  for_each       = toset(["quant-trend-lab/api", "quant-trend-lab/web"])
  compartment_id = var.compartment_ocid
  display_name   = each.value
  is_public      = false
  freeform_tags  = var.freeform_tags
}

resource "oci_kms_vault" "application" {
  compartment_id = var.compartment_ocid
  display_name   = "quant-trend-lab"
  vault_type     = "DEFAULT"
  freeform_tags  = var.freeform_tags
  lifecycle { prevent_destroy = true }
}

resource "oci_kms_key" "application" {
  compartment_id      = var.compartment_ocid
  display_name        = "quant-trend-lab"
  management_endpoint = oci_kms_vault.application.management_endpoint
  key_shape {
    algorithm = "AES"
    length    = 32
  }
  protection_mode = "SOFTWARE"
  freeform_tags   = var.freeform_tags
  lifecycle { prevent_destroy = true }
}

resource "oci_ons_notification_topic" "operations" {
  compartment_id = var.compartment_ocid
  name           = "quant-trend-lab-operations"
  description    = "Quant Trend Lab operational alarms"
  freeform_tags  = var.freeform_tags
}

resource "oci_ons_subscription" "email" {
  count          = var.notification_email == null ? 0 : 1
  compartment_id = var.compartment_ocid
  topic_id       = oci_ons_notification_topic.operations.id
  protocol       = "EMAIL"
  endpoint       = var.notification_email
}

resource "oci_monitoring_alarm" "cpu" {
  compartment_id        = var.compartment_ocid
  destinations          = [oci_ons_notification_topic.operations.id]
  display_name          = "quant-trend-lab-high-cpu"
  is_enabled            = true
  metric_compartment_id = var.compartment_ocid
  namespace             = "oci_computeagent"
  query                 = "CpuUtilization[5m]{resourceId = \"${var.instance_ocid}\"}.mean() > 80"
  severity              = "WARNING"
  pending_duration      = "PT15M"
  body                  = "CPU utilization exceeded 80% for 15 minutes."
  freeform_tags         = var.freeform_tags
}

resource "oci_monitoring_alarm" "memory" {
  compartment_id        = var.compartment_ocid
  destinations          = [oci_ons_notification_topic.operations.id]
  display_name          = "quant-trend-lab-high-memory"
  is_enabled            = true
  metric_compartment_id = var.compartment_ocid
  namespace             = "oci_computeagent"
  query                 = "MemoryUtilization[5m]{resourceId = \"${var.instance_ocid}\"}.mean() > 85"
  severity              = "WARNING"
  pending_duration      = "PT15M"
  body                  = "Memory utilization exceeded 85% for 15 minutes."
  freeform_tags         = var.freeform_tags
}
