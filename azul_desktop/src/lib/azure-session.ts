import {
  connectAzure,
  hydrateAzureKeyVaultSecrets,
} from "./api";
import { loginWithMicrosoftForAzure, loginWithMicrosoftForKeyVault } from "./azure-auth";
import type { BackendAuthStatus, HatchingProfile } from "./contracts";

function azureConfigFromProfile(profile: HatchingProfile): Record<string, string> {
  return profile.skill_configs?.Azure ?? {};
}

export function profileCanRenewAzureLogin(profile: HatchingProfile): boolean {
  const config = azureConfigFromProfile(profile);
  return (
    (config.authMethod?.trim() || "entra") === "entra" &&
    config.connected === "true" &&
    Boolean(config.clientId?.trim()) &&
    Boolean(config.endpoint?.trim()) &&
    Boolean(config.deployment?.trim())
  );
}

export async function renewAzureLoginFromProfile(profile: HatchingProfile): Promise<BackendAuthStatus> {
  const config = azureConfigFromProfile(profile);
  if (!profileCanRenewAzureLogin(profile)) {
    throw new Error("Azure profile is incomplete. Open Settings > Azure to finish configuration.");
  }

  const tenantId = config.tenantId?.trim() ?? "";
  const clientId = config.clientId.trim();
  const endpoint = config.endpoint.trim().replace(/\/$/, "");
  const keyVaultUrl = (config.keyVaultUrl ?? "").trim().replace(/\/$/, "");

  const login = await loginWithMicrosoftForAzure({ tenantId, clientId });
  if (keyVaultUrl) {
    try {
      const keyVaultLogin = await loginWithMicrosoftForKeyVault({ tenantId, clientId });
      await hydrateAzureKeyVaultSecrets({
        key_vault_url: keyVaultUrl,
        access_token: keyVaultLogin.accessToken,
        expires_on: keyVaultLogin.expiresOn,
        microsoft_app_id_secret_name: (config.microsoftAppIdSecretName ?? "").trim(),
        microsoft_app_password_secret_name: (config.microsoftAppPasswordSecretName ?? "").trim(),
        microsoft_app_tenant_id_secret_name: (config.microsoftAppTenantIdSecretName ?? "").trim(),
      });
    } catch (error) {
      console.warn("Azure Key Vault hydration failed during login renewal.", error);
    }
  }

  return connectAzure({
    tenant_id: tenantId,
    client_id: clientId,
    endpoint,
    deployment: config.deployment.trim(),
    fast_deployment: (config.fastDeployment ?? "").trim(),
    embedding_deployment: (config.embeddingDeployment ?? "").trim(),
    key_vault_url: keyVaultUrl,
    access_token: login.accessToken,
    expires_on: login.expiresOn,
    scope: login.scope,
  });
}
