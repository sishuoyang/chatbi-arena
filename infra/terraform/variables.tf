variable "region" {
  type    = string
  default = "ap-southeast-1"
}

variable "aws_profile" {
  type        = string
  default     = "sa"
  description = "AWS CLI profile with rights in the target account (959934561610)."
}

variable "name_prefix" {
  type    = string
  default = "chatbi-arena"
}

variable "db_name" {
  type    = string
  default = "arena"
}

variable "master_username" {
  type    = string
  default = "arena_admin"
}

variable "aurora_engine_version" {
  type        = string
  default     = "16.4"
  description = "Aurora PostgreSQL engine version (check availability in-region)."
}

variable "aurora_pg_family" {
  type        = string
  default     = "aurora-postgresql16"
  description = "Cluster parameter group family; must match the engine major version."
}

variable "min_acu" {
  type    = number
  default = 0.5
}

variable "max_acu" {
  type    = number
  default = 2
}

variable "admin_ingress_cidr" {
  type        = string
  description = "Your public IP in CIDR form (e.g. 1.2.3.4/32) for data-gen writes."
}

variable "clickpipes_ingress_cidrs" {
  type        = list(string)
  default     = []
  description = <<-EOT
    ClickHouse Cloud / ClickPipes static egress IPs (CIDR) for your region.
    Find them in the ClickPipes 'create pipe' wizard or ClickHouse docs
    (Cloud endpoints reference), then list them here so CDC can reach Aurora.
  EOT
}
