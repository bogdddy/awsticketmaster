# ═══════════════════════════════════════════════════════════════════
# AWSTicket — AWS Academy Learner Lab
# ═══════════════════════════════════════════════════════════════════

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region     = var.aws_region
  access_key = var.aws_access_key_id
  secret_key = var.aws_secret_access_key
  token      = var.aws_session_token
}

data "aws_caller_identity" "current" {}

# ── VPC y red ────────────────────────────────────────────────────

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "${var.project_name}-vpc" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.subnet_cidr
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = true

  tags = { Name = "${var.project_name}-public-subnet" }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${var.project_name}-igw" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }

  tags = { Name = "${var.project_name}-public-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# ── Locales ──────────────────────────────────────────────────────

locals {
  lab_role_arn         = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/LabRole"
  lab_instance_profile = "LabInstanceProfile"
}

# ── Security Groups ─────────────────────────────────────────────

module "security" {
  source = "./modules/security"

  vpc_id       = aws_vpc.main.id
  vpc_cidr     = var.vpc_cidr
  project_name = var.project_name
}

# ── S3 (resultados) ─────────────────────────────────────────────

module "storage" {
  source = "./modules/storage"

  project_name     = var.project_name
  create_s3_bucket = true
}

# ── ECR (imagen worker) ─────────────────────────────────────────

module "ecr" {
  source = "./modules/ecr"

  project_name = var.project_name
}

# ── RabbitMQ en EC2 ─────────────────────────────────────────────

module "rabbitmq" {
  source = "./modules/rabbitmq"

  project_name         = var.project_name
  subnet_id            = aws_subnet.public.id
  security_group_id    = module.security.rabbitmq_sg_id
  instance_type        = var.rabbitmq_instance_type
  key_name             = var.key_name
  iam_instance_profile = local.lab_instance_profile
  rabbitmq_user        = var.rabbitmq_user
  rabbitmq_password    = var.rabbitmq_password
  ami_id               = var.ami_id
  private_ip           = var.rabbitmq_private_ip
}

# ── PostgreSQL en EC2 ───────────────────────────────────────────

module "postgres" {
  source = "./modules/postgres"

  project_name         = var.project_name
  vpc_cidr             = var.vpc_cidr
  subnet_id            = aws_subnet.public.id
  security_group_id    = module.security.postgres_sg_id
  instance_type        = var.postgres_instance_type
  key_name             = var.key_name
  iam_instance_profile = local.lab_instance_profile
  db_name              = var.postgres_db_name
  db_user              = var.postgres_user
  db_password          = var.postgres_password
  ami_id               = var.ami_id
  private_ip           = var.postgres_private_ip
}

# ── Workers Fargate ─────────────────────────────────────────────

module "workers" {
  source = "./modules/workers"

  project_name          = var.project_name
  subnet_ids            = [aws_subnet.public.id]
  security_group_id     = module.security.worker_sg_id
  lab_role_arn          = local.lab_role_arn
  ecr_repository_url    = module.ecr.repository_url
  rabbitmq_endpoint     = module.rabbitmq.private_ip
  postgres_endpoint     = module.postgres.private_ip
  rabbitmq_user         = var.rabbitmq_user
  rabbitmq_password     = var.rabbitmq_password
  postgres_user         = var.postgres_user
  postgres_password     = var.postgres_password
  postgres_db_name      = var.postgres_db_name
  worker_cpu            = var.worker_cpu
  worker_memory         = var.worker_memory
  worker_desired_count  = var.worker_desired_count
  worker_min_count      = var.worker_min_count
  worker_max_count      = var.worker_max_count
  aws_region            = var.aws_region
}

# ── Autoscaling Controller ──────────────────────────────────────

module "autoscaling" {
  source = "./modules/autoscaling"

  project_name                = var.project_name
  rabbitmq_endpoint           = module.rabbitmq.private_ip
  rabbitmq_user               = var.rabbitmq_user
  rabbitmq_password           = var.rabbitmq_password
  ecs_cluster_name            = module.workers.cluster_name
  ecs_service_name            = module.workers.service_name
  lab_role_arn                = local.lab_role_arn
  subnet_ids                  = [aws_subnet.public.id]
  security_group_id           = module.security.worker_sg_id
  target_backlog_per_worker   = var.target_backlog_per_worker
  worker_min_count            = var.worker_min_count
  worker_max_count            = var.worker_max_count
}

# ── Load Generator EC2 ──────────────────────────────────────────

module "loadgen" {
  source = "./modules/loadgen"

  project_name         = var.project_name
  subnet_id            = aws_subnet.public.id
  security_group_id    = module.security.loadgen_sg_id
  instance_type        = var.loadgen_instance_type
  key_name             = var.key_name
  iam_instance_profile = local.lab_instance_profile
  rabbitmq_endpoint    = module.rabbitmq.private_ip
  rabbitmq_user        = var.rabbitmq_user
  rabbitmq_password    = var.rabbitmq_password
  ami_id               = var.ami_id
}

# ── CloudWatch Dashboard ────────────────────────────────────────

module "observability" {
  source = "./modules/observability"

  project_name         = var.project_name
  ecs_cluster_name     = module.workers.cluster_name
  ecs_service_name     = module.workers.service_name
  rabbitmq_instance_id = module.rabbitmq.instance_id
  postgres_instance_id = module.postgres.instance_id
  loadgen_instance_id  = module.loadgen.instance_id
  s3_bucket_id         = module.storage.bucket_id
  log_group_name       = module.workers.log_group_name
}
