Amazon Virtual Private Cloud (Amazon VPC)
	Elastic IP address quota per NAT gateway	10
	NAT gateways per Availability Zone		30
	Internet gateways per Region			20
Amazon Elastic Compute Cloud (Amazon EC2)
	EC2-VPC Elastic IPs				50
	All Inf Spot Instance Requests		128

User: cloud-provisioner (Full fast access to development deploy)
User: cloud-provisioner-eks (Only EKS deployments)
User: cloud-provisioner-aws (Only AWS deployments)

# Resumen tareas al crear una nueva cuenta

* [Resumen tareas al crear una nueva cuenta](#resumen-tareas-al-crear-una-nueva-cuenta)
	* [Quotas](#quotas)

## Quotas

Lo primero que haremos será solicitar un aumento de cuotas para los siguientes recursos:

| Recurso | Cantidad |
|---------|----------|
| Elastic IP address quota per NAT gateway | 8 |
| NAT gateways per Availability Zone | 30 |
| Internet gateways per Region | 20 |
| EC2-VPC Elastic IPs | 50 |
| All Inf Spot Instance Requests | 128 |


Así comprobaremos el estado actual de las quotas:

Pre-requisitos usuario con los permisos necesarios para poder hacer las queries a la API de AWS.

| Service | Access level |
|---------|--------------|
| Service Quotas | Full Access |

## Get service quotas

```bash
# Elastic IP address quota per NAT gateway
aws service-quotas get-service-quota --service-code vpc --quota-code L-5F53652F | jq '.Quota.Value' | cat
# NAT gateways per Availability Zone
aws service-quotas get-service-quota --service-code vpc --quota-code L-026E1A4D | jq '.Quota.Value' | cat
# Internet gateways per Region
aws service-quotas get-service-quota --service-code vpc --quota-code L-A4707A72 | jq '.Quota.Value' | cat
# EC2-VPC Elastic IPs
aws service-quotas get-service-quota --service-code ec2 --quota-code L-0263D0A3 | jq '.Quota.Value' | cat
# All Inf Spot Instance Requests
aws service-quotas get-service-quota --service-code ec2 --quota-code L-B5D1601B | jq '.Quota.Value' | cat
```

# IAM

