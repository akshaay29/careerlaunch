"use client";

import { useState } from "react";

import { ProviderKeyManager } from "@/components/provider-key/provider-key-manager";
import { ProviderUsageSection } from "@/components/provider-key/provider-usage";

/**
 * The key manager and the usage section together: saving or deleting the key changes the
 * Student's pause status, so the usage section refetches when the key changes.
 */
export function ProviderKeyPanel() {
  const [keyVersion, setKeyVersion] = useState(0);
  return (
    <>
      <ProviderKeyManager onChanged={() => setKeyVersion((v) => v + 1)} />
      <ProviderUsageSection refreshKey={keyVersion} />
    </>
  );
}
