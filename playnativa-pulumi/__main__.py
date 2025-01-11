"""An AWS Python Pulumi program"""

import pulumi
import pulumi_aws as aws
import pulumi_docker as docker
import os

# Parámetros clave
region = "us-east-1"
vpc_id = "vpc-0b56fcc9755dfcedc"
subnets = [
    "subnet-0316ab2b42936e016",  # us-east-1b
    "subnet-02b8390c4596e8cf2",  # us-east-1c
]
domain_name = "playnativa.cl"
cluster_name = "playnativa-cluster"

# Crear un Security Group que permita tráfico en el puerto 8000
security_group = aws.ec2.SecurityGroup(
    "playnativa-sg",
    vpc_id=vpc_id,
    description="Security group for ALB and tasks",
    ingress=[
        # ALB inbound
        {
            "protocol": "tcp",
            "from_port": 443,
            "to_port": 443,
            "cidr_blocks": ["0.0.0.0/0"],  # HTTPS from the world
        },
        # HTTP inbound
        {
            "protocol": "tcp",
            "from_port": 80,
            "to_port": 80,
            "cidr_blocks": ["0.0.0.0/0"],  # HTTP from the world
        },
        # ECS tasks inbound on 8000 from the ALB
        {
            "protocol": "tcp",
            "from_port": 8000,
            "to_port": 8000,
            "cidr_blocks": ["0.0.0.0/0"],  # Less restrictive, or from ALB SG specifically
        }
    ],
    egress=[
        {
            "protocol": "-1",
            "from_port": 0,
            "to_port": 0,
            "cidr_blocks": ["0.0.0.0/0"],
        }
    ],
    tags={"Name": "playnativa-sg"},
)

# Repositorio ECR
ecr_repo = aws.ecr.Repository("playnativa-ecr")

# Construir imagen de aplicación
image = docker.Image(
    "playnativa-image",
    build={
        "context": "../",
        "dockerfile": "../Dockerfile",
        "platform": "linux/amd64",
    },
    image_name=ecr_repo.repository_url.apply(lambda url: f"{url}:{os.getenv('GITHUB_SHA', 'latest')}"),
    registry=ecr_repo.registry_id.apply(lambda _: aws.ecr.get_authorization_token().proxy_endpoint),
)

# Crear un ECS Cluster
ecs_cluster = aws.ecs.Cluster(cluster_name)

# Crear un Target Group
target_group = aws.lb.TargetGroup(
    "playnativa-tg",
    protocol="HTTP",
    port=80,
    target_type="ip",
    vpc_id=vpc_id,
    health_check={
        "protocol": "HTTP",
        "path": "/",
        "interval": 30,
        "timeout": 5,
    },
)

# ALB
load_balancer = aws.lb.LoadBalancer(
    "playnativa-lb",
    security_groups=[security_group.id],
    subnets=subnets,
    enable_deletion_protection=False,
)

# Listener para HTTPS
listener = aws.lb.Listener(
    "playnativa-listener",
    load_balancer_arn=load_balancer.arn,
    protocol="HTTPS",
    port=443,
    ssl_policy="ELBSecurityPolicy-2016-08",
    certificate_arn="arn:aws:acm:us-east-1:307154673918:certificate/7d7788c7-559c-48ef-8a1a-348f4acab0c6",  # ARN del certificado SSL
    default_actions=[{
        "type": "forward",
        "target_group_arn": target_group.arn,
    }],
)


# Rol de ejecución para ECS
execution_role = aws.iam.Role("ecsTaskExecutionRole", assume_role_policy="""{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": { "Service": "ecs-tasks.amazonaws.com" },
            "Action": "sts:AssumeRole"
        }
    ]
}""")

# Políticas para el rol
aws.iam.RolePolicyAttachment("ecsTaskExecutionRolePolicy",
    role=execution_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy")


task_role = aws.iam.Role(
    "ecsTaskRole",
    assume_role_policy="""{
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": "ecs-tasks.amazonaws.com"
                },
                "Action": "sts:AssumeRole"
            }
        ]
    }"""
)

aws.iam.RolePolicyAttachment(
    "ssmManagedPolicy",
    role=task_role.name,
    policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
)

aws.iam.RolePolicyAttachment(
    "ecsExecuteCommandPolicy",
    role=task_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
)


# Crear EFS para archivos estáticos
efs = aws.efs.FileSystem(
    "playnativa-efs",
    encrypted=True,
    tags={"Name": "playnativa-staticfiles"}
)

# Crear punto de montaje EFS
efs_mount_target = aws.efs.MountTarget(
    "playnativa-efs-mount-target",
    file_system_id=efs.id,
    subnet_id=subnets[0],
    security_groups=[security_group.id]
)

# Configurar definición de tarea ECS
task_definition = aws.ecs.TaskDefinition(
    "playnativa-task",
    family="playnativa",
    cpu="1024",
    memory="2048",
    network_mode="awsvpc",
    execution_role_arn=execution_role.arn,
    task_role_arn=task_role.arn,
    container_definitions=image.image_name.apply(
        lambda image: f"""[
            {{
                "name": "playnativa",
                "image": "{image}",
                "essential": true,
                "portMappings": [
                    {{
                        "containerPort": 8000,
                        "protocol": "tcp"
                    }}
                ],
                "environment": [
                    {{
                        "name": "DJANGO_ENV",
                        "value": "prod"
                    }}
                ],
                "mountPoints": [
                    {{
                        "sourceVolume": "staticfiles",
                        "containerPath": "/app/staticfiles"
                    }}
                ],
                "command": [
                    "sh",
                    "-c",
                    "mkdir -p /app/staticfiles && python manage.py collectstatic --noinput && gunicorn playnativa_project.wsgi:application --bind 0.0.0.0:8000"
                ]
            }},
            {{
                "name": "nginx",
                "image": "nginx:latest",
                "essential": true,
                "portMappings": [
                    {{
                        "containerPort": 80,
                        "protocol": "tcp"
                    }}
                ],
                "mountPoints": [
                    {{
                        "sourceVolume": "staticfiles",
                        "containerPath": "/app/staticfiles"
                    }}
                ],
                "command": ["nginx", "-g", "daemon off;"],
                "environment": [
                    {{
                        "name": "STATIC_ROOT",
                        "value": "/app/staticfiles"
                    }}
                ]
            }}
        ]"""
    ),
    volumes=[{
        "name": "staticfiles",
        "efsVolumeConfiguration": {
            "fileSystemId": efs.id,
            "rootDirectory": "/",
            "transitEncryption": "ENABLED"
        }
    }]
)

# Crear servicio ECS
ecs_service = aws.ecs.Service(
    "playnativa-service",
    cluster=ecs_cluster.arn,
    desired_count=1,
    launch_type="FARGATE",
    task_definition=task_definition.arn,
    enable_execute_command=True,
    network_configuration={
        "assignPublicIp": True,
        "subnets": subnets,
        "securityGroups": [security_group.id],
    },
    load_balancers=[{
        "targetGroupArn": target_group.arn,
        "containerName": "playnativa",
        "containerPort": 8000,
    }],
)

# Configurar Route 53
zone = aws.route53.get_zone(name=domain_name)

record = aws.route53.Record(
    "playnativa-record",
    zone_id=zone.zone_id,
    name=domain_name,
    type="A",
    aliases=[{
        "name": load_balancer.dns_name,
        "zone_id": load_balancer.zone_id,
        "evaluate_target_health": True,
    }],
    opts=pulumi.ResourceOptions(replace_on_changes=["aliases"]),
)

# Agregar un registro CNAME para www.playnativa.cl
cname_record = aws.route53.Record(
    "www-playnativa-record",
    zone_id=zone.zone_id,
    name="www.playnativa.cl",  # Nombre del subdominio
    type="CNAME",
    ttl=300,  # Tiempo de vida (en segundos)
    records=[domain_name],  # Apunta a playnativa.cl
)

