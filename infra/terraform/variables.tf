variable "tenancy_ocid" {
  description = "OCI tenancy OCID"
  type        = string
}

variable "compartment_ocid" {
  description = "Application compartment OCID"
  type        = string
}

variable "instance_ocid" {
  description = "Existing ARM64 compute instance OCID; Terraform never creates or destroys it"
  type        = string
}

variable "object_storage_namespace" {
  description = "Existing Object Storage namespace"
  type        = string
}

variable "bucket_name" {
  description = "Existing private bucket; Terraform reads it but does not own it"
  type        = string
}

variable "vcn_ocid" {
  description = "VCN containing the existing instance"
  type        = string
}

variable "region" {
  description = "OCI region"
  type        = string
  default     = "ap-seoul-1"
}

variable "management_cidr" {
  description = "Fixed administrator CIDR allowed to reach SSH"
  type        = string
  validation {
    condition     = can(cidrhost(var.management_cidr, 0))
    error_message = "management_cidr must be a valid CIDR."
  }
}

variable "block_volume_size_gb" {
  description = "Application data volume size"
  type        = number
  default     = 100
  validation {
    condition     = var.block_volume_size_gb >= 50
    error_message = "Block volume must be at least 50 GB."
  }
}

variable "notification_email" {
  description = "Optional alarm subscription email"
  type        = string
  default     = null
  nullable    = true
}

variable "freeform_tags" {
  description = "Tags applied to resources created by this stack"
  type        = map(string)
  default = {
    project    = "quant-trend-lab"
    managed_by = "terraform"
  }
}
