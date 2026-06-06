output "aurora_endpoint" {
  value       = aws_rds_cluster.arena.endpoint
  description = "Writer endpoint host for Aurora."
}

output "aurora_port" {
  value = aws_rds_cluster.arena.port
}

output "aurora_database" {
  value = aws_rds_cluster.arena.database_name
}

output "aurora_master_username" {
  value = var.master_username
}

output "aurora_master_password" {
  value     = random_password.master.result
  sensitive = true
}

# Convenience: a ready-to-paste DSN (password redacted unless -raw).
output "aurora_dsn" {
  value     = "postgresql://${var.master_username}:${random_password.master.result}@${aws_rds_cluster.arena.endpoint}:${aws_rds_cluster.arena.port}/${aws_rds_cluster.arena.database_name}"
  sensitive = true
}
