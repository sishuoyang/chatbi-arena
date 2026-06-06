terraform {
  required_version = ">= 1.5"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.60" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile
}

# --- Networking: reuse the default VPC for a POC (no bespoke VPC) -------------
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_db_subnet_group" "arena" {
  name       = "${var.name_prefix}-subnets"
  subnet_ids = data.aws_subnets.default.ids
}

# Security group: Postgres 5432 from ClickPipes egress IPs + your admin IP.
# ClickHouse Cloud publishes static NAT egress IPs per region; put them in
# var.clickpipes_ingress_cidrs. var.admin_ingress_cidr is your IP for data-gen.
resource "aws_security_group" "arena" {
  name        = "${var.name_prefix}-pg"
  description = "ChatBI Arena Aurora Postgres ingress"
  vpc_id      = data.aws_vpc.default.id

  dynamic "ingress" {
    for_each = toset(concat(var.clickpipes_ingress_cidrs, [var.admin_ingress_cidr]))
    content {
      description = "Postgres"
      from_port   = 5432
      to_port     = 5432
      protocol    = "tcp"
      cidr_blocks = [ingress.value]
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- Credentials --------------------------------------------------------------
resource "random_password" "master" {
  length  = 20
  special = false
}

# --- Aurora PostgreSQL Serverless v2 -----------------------------------------
# Cluster parameter group enables logical replication (required for CDC).
resource "aws_rds_cluster_parameter_group" "arena" {
  name        = "${var.name_prefix}-cluster-pg"
  family      = var.aurora_pg_family
  description = "ChatBI Arena - enable logical replication for ClickPipes CDC"

  parameter {
    name         = "rds.logical_replication"
    value        = "1"
    apply_method = "pending-reboot"
  }
}

resource "aws_rds_cluster" "arena" {
  cluster_identifier              = "${var.name_prefix}-pg"
  engine                          = "aurora-postgresql"
  engine_version                  = var.aurora_engine_version
  database_name                   = var.db_name
  master_username                 = var.master_username
  master_password                 = random_password.master.result
  db_subnet_group_name            = aws_db_subnet_group.arena.name
  vpc_security_group_ids          = [aws_security_group.arena.id]
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.arena.name
  skip_final_snapshot             = true
  apply_immediately               = true

  serverlessv2_scaling_configuration {
    min_capacity = var.min_acu
    max_capacity = var.max_acu
  }
}

resource "aws_rds_cluster_instance" "arena" {
  identifier          = "${var.name_prefix}-pg-1"
  cluster_identifier  = aws_rds_cluster.arena.id
  instance_class      = "db.serverless"
  engine              = aws_rds_cluster.arena.engine
  engine_version      = aws_rds_cluster.arena.engine_version
  publicly_accessible = true
}
