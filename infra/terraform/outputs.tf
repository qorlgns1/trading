output "existing_instance_shape" {
  description = "Confirm this is at least 2 OCPU and 8 GB RAM before deployment"
  value       = data.oci_core_instance.existing.shape
}

output "existing_instance_ocpus" {
  value = try(data.oci_core_instance.existing.shape_config[0].ocpus, null)
}

output "existing_instance_memory_gb" {
  value = try(data.oci_core_instance.existing.shape_config[0].memory_in_gbs, null)
}

output "existing_bucket_name" {
  value = data.oci_objectstorage_bucket.existing.name
}

output "application_nsg_ocid" {
  description = "Attach this NSG to the existing instance VNIC after the first apply"
  value       = oci_core_network_security_group.application.id
}

output "block_volume_ocid" {
  value = oci_core_volume.application.id
}

output "vault_ocid" {
  value = oci_kms_vault.application.id
}

output "vault_key_ocid" {
  value = oci_kms_key.application.id
}

output "ocir_repositories" {
  value = { for name, repository in oci_artifacts_container_repository.application : name => repository.id }
}
