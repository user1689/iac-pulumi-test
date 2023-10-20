"""An AWS Python Pulumi program"""

import pulumi
import pulumi_aws as aws
import subprocess
import json
import ipaddress
import pulumi_tls as tls
import subprocess

from pulumi import ResourceOptions
from pulumi_aws.ec2 import InstanceRootBlockDeviceArgs


def run_aws_cli_command(command):
    try:
        result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True)
        print("AWS CLI command executed successfully. Output:")
        return json.loads(result.stdout)["AvailabilityZones"]
    except subprocess.CalledProcessError as e:
        print("Error: AWS CLI command failed. Error message:")
        print(e.stderr)


config_aws = pulumi.Config("aws")
config_vpc = pulumi.Config("iac-pulumi")
data = config_vpc.require_object("data")
tags = pulumi.Config("tags")
tags_name = tags.require_object("name")

aws_command = f"aws ec2 describe-availability-zones --region {config_aws.get('region')}"
all_az = run_aws_cli_command(aws_command)
number_of_all_az = len(all_az)
print(f"total number of az in this region is {number_of_all_az}")

# step1: create a VPC
vpc_cidr_value = data.get("vpc_cidr")
vpc = aws.ec2.Vpc(resource_name=data.get("vpc_name"),
                  cidr_block=vpc_cidr_value,
                  tags={
                      "Name": tags_name.get("vpc")
                  })

# step2: create an internet Gateway
internet_gateway = aws.ec2.InternetGateway(resource_name=data.get("internet_gateway_name"),
                                           vpc_id=vpc.id,
                                           tags={
                                               "Name": tags_name.get("internet_gateway")
                                           })

# step3: create public subnet route table and private subnet route table
public_subnets_route_table = aws.ec2.RouteTable(resource_name=data.get("public_subnets_route_table_name"),
                                                vpc_id=vpc.id,
                                                routes=[
                                                    aws.ec2.RouteTableRouteArgs(cidr_block=data.get("default_route"),
                                                                                gateway_id=internet_gateway.id)],
                                                tags={
                                                    "Name": tags_name.get("public_route_table")
                                                })

private_subnets_route_table = aws.ec2.RouteTable(resource_name=data.get("private_subnets_route_table_name"),
                                                 vpc_id=vpc.id,
                                                 tags={
                                                     "Name": tags_name.get("private_route_table")
                                                 })

# step4: create public subnets and private subnets, then add an association with route table for each
number_of_subnets_for_each = data.get("num_subnets_for_each")
public_subnets = [None for i in range(number_of_subnets_for_each)]
private_subnets = [None for i in range(number_of_subnets_for_each)]
if number_of_all_az < number_of_subnets_for_each: raise Exception("the number of AZ isn't enough for subnets")
if int(vpc_cidr_value.split('/')[1]) >= int(data.get("subnet_mask")): raise Exception("subnet mask of subnet should "
                                                                                      "bigger than subnet mask of vpc")

# step5: generate subnets
vpc_network = ipaddress.ip_network(data.get("vpc_cidr"))
subnets = list(vpc_network.subnets(new_prefix=int(data.get("subnet_mask"))))

for i in range(0, number_of_subnets_for_each):
    public_subnets[i] = aws.ec2.Subnet(resource_name=data.get("public_subnet_name") + str(i),
                                       vpc_id=vpc.id,
                                       cidr_block=subnets[i].__str__(),
                                       availability_zone_id=all_az[i]["ZoneId"],
                                       map_public_ip_on_launch=False,
                                       tags={
                                           "Name": tags_name.get("public_subnet") + str(i)
                                       })
    aws.ec2.RouteTableAssociation(
        resource_name=data.get("public_subnets_route_table_association") + str(i),
        route_table_id=public_subnets_route_table.id,
        subnet_id=public_subnets[i].id
    )

    private_subnets[i] = aws.ec2.Subnet(resource_name=data.get("private_subnet_name") + str(i),
                                        vpc_id=vpc.id,
                                        cidr_block=subnets[i + number_of_subnets_for_each].__str__(),
                                        availability_zone_id=all_az[i]["ZoneId"],
                                        map_public_ip_on_launch=False,
                                        tags={
                                            "Name": tags_name.get("private_subnet") + str(i)
                                        })

    aws.ec2.RouteTableAssociation(
        resource_name=data.get("private_subnets_route_table_association") + str(i),
        route_table_id=private_subnets_route_table.id,
        subnet_id=private_subnets[i].id
    )

security_group = aws.ec2.SecurityGroup(
    resource_name=data.get("security_group_name"),
    description="Allow traffic to 80, 443, 22, 8080 port",
    ingress=[
        {
            "protocol": "tcp",
            "from_port": 8080,
            "to_port": 8080,
            "cidr_blocks": [data.get("default_route")],
        },
        {
            "protocol": "tcp",
            "from_port": 80,
            "to_port": 80,
            "cidr_blocks": [data.get("default_route")],
        },
        {
            "protocol": "tcp",
            "from_port": 443,
            "to_port": 443,
            "cidr_blocks": [data.get("default_route")],
        },
        {
            "protocol": "tcp",
            "from_port": 22,
            "to_port": 22,
            "cidr_blocks": [data.get("default_route")],
        }
    ],
    egress=[
        {
            "protocol": "-1",
            "from_port": 0,
            "to_port": 0,
            "cidr_blocks": [data.get("default_route")],
        }
    ],
    tags={
        "name": data.get("security_group_name")
    },
    vpc_id=vpc.id
)

ami = aws.ec2.get_ami(
    most_recent="true",
    owners=[data.get("ec2_owner_account_id")],
    tags={
        "name": data.get("latest_ami_name")
    }
)

# TODO: refactor hardcode, double check requirement
# user_data = """
# #!/bin/bash
# cd ~
# nohup java -jar -Dspring.profiles.active=demo webapp-1.0-SNAPSHOT.jar>webapp.log 2>&1 &
# """

ssh_key = tls.PrivateKey(
    resource_name=data.get("ssh_key_name"),
    algorithm=data.get("key_algo"),
    rsa_bits=int(data.get("rsa_bits"))
)

aws_key = aws.ec2.KeyPair(
    resource_name=data.get("aws_key_name"),
    key_name=data.get("key_name"),
    public_key=ssh_key.public_key_openssh,
    opts=pulumi.ResourceOptions(parent=ssh_key),
    tags={
        "name": data.get("key_name")
    }
)

ec2_instance = aws.ec2.Instance(
    ami=ami.id,
    resource_name=data.get("ec2_instance_name"),
    instance_type=data.get("ec2_instance_type"),
    vpc_security_group_ids=[security_group.id],
    key_name=aws_key.key_name,
    subnet_id=public_subnets[0].id,
    associate_public_ip_address=True,
    root_block_device=InstanceRootBlockDeviceArgs(
        delete_on_termination=True,
        volume_size=int(data.get("ec2_instance_root_volume_size")),
        volume_type=data.get("ec2_instance_root_volume_type")
    )
)

# Export the name of the resources
pulumi.export('vpc_name', vpc.id)
for i in range(0, number_of_subnets_for_each): pulumi.export(f"private_subnets_{i}_name", private_subnets[i].id)
for i in range(0, number_of_subnets_for_each): pulumi.export(f"public_subnets_{i}_name", public_subnets[i].id)
pulumi.export("ec2_instance", ec2_instance.id)
pulumi.export('private_key_pem', ssh_key.private_key_pem)
pulumi.export('public_ip', ec2_instance.public_ip)