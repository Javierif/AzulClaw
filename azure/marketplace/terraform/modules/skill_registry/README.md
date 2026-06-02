# Skill Registry Module

Provisiona la base Azure del Skill Registry:

- Resource Group
- Storage Account
- Blob container para `.azulskill`
- Table Storage para metadata/versiones
- Linux Function App
- Application Insights
- Key Vault

Los entornos consumen este módulo desde:

- `azure/marketplace/terraform/envs/dev`
- `azure/marketplace/terraform/envs/prod`
