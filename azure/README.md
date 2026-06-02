# Azure Platform

`azure/` contains Azure platform code shared by AzulClaw itself. It does not
own skill-specific runtime or deployment code.

## Layout

```text
azure/
|- core/             Base Azure resources for AzulClaw as a product
|- marketplace/      Skill Registry API and marketplace infrastructure
`- shared/           Reusable Terraform modules
```

## Ownership

- `azure/core/` is for base AzulClaw resources that are not tied to a specific
  skill.
- `azure/marketplace/` is for the private Skill Registry API, artifact storage,
  registry identity, signing, and marketplace telemetry.
- `azure/shared/terraform/modules/` contains Terraform modules reused by core,
  marketplace, and skills.
- Skill-specific code belongs under `skills/official/<skill>/`.

For example, the Telegram Bot relay Function now lives at:

```text
skills/official/telegram/src/relay_function/
```

Its deployment scaffolding lives at:

```text
skills/official/telegram/infra/terraform/
```

## Related documentation

- [Azure Bot Architecture](../docs/12_azure_bot_architecture.md)
- [Azure Bot Deployment Guide](../docs/13_azure_bot_deployment_guide.md)
- [Channels and Transport](../docs/14_channels_and_transport.md)
