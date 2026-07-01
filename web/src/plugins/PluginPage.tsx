import { useState, useEffect } from "react";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import {
  getPluginComponent,
  getPluginLoadError,
  onPluginRegistered,
} from "./registry";
import { useI18n } from "@/i18n";
import { cn } from "@/lib/utils";
import type { Translations } from "@/i18n/types";

/** Renders a plugin tab once its bundle has called `register()`. */
export function PluginPage({ name }: { name: string }) {
  const { t } = useI18n();
  // Lazy initialisers capture plugins already registered at mount time;
  // the effect subscribes to future registrations without useSyncExternalStore
  // tearing-detection re-renders (which caused React error #301).
  const [Component, setComponent] = useState<React.ComponentType | null>(
    () => getPluginComponent(name) ?? null,
  );
  const [loadError, setLoadError] = useState<string | null>(
    () => getPluginLoadError(name) ?? null,
  );
  useEffect(() => {
    return onPluginRegistered(() => {
      setComponent(() => getPluginComponent(name) ?? null);
      setLoadError(getPluginLoadError(name) ?? null);
    });
  }, [name]);

  if (Component) {
    return <Component />;
  }

  if (loadError) {
    const message = formatPluginError(loadError, t);
    return (
      <div
        className={cn(
          "max-w-lg p-4",
          "font-mondwest text-sm tracking-[0.08em] text-text-secondary",
        )}
        role="alert"
      >
        {message}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex items-center gap-2 p-4",
        "font-mondwest text-sm tracking-[0.1em] text-text-tertiary",
      )}
    >
      <Spinner className="shrink-0" />
      <span>{t.common.loading}</span>
    </div>
  );
}

function formatPluginError(code: string, t: Translations): string {
  if (code === "LOAD_FAILED") return t.common.pluginLoadFailed;
  if (code === "NO_REGISTER") return t.common.pluginNotRegistered;
  return code;
}
