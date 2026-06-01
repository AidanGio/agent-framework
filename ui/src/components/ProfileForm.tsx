import { useEffect, useState } from "react";

import type { ModelOption, Profile, ProfileUpdate } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface ProfileFormProps {
  models: Array<ModelOption>;
  initial: Profile;
  onSubmit: (body: ProfileUpdate) => Promise<unknown>;
  saving: boolean;
  error: string | null;
}

export function ProfileForm({ models, initial, onSubmit, saving, error }: ProfileFormProps) {
  const first: ModelOption | undefined = models[0];
  const [modelId, setModelId] = useState<string>(initial.default_model ?? first?.id ?? "");
  const currentModel: ModelOption | undefined = models.find((m) => m.id === modelId) ?? first;
  const [effort, setEffort] = useState<string>(
    initial.reasoning_effort ?? currentModel?.default_effort ?? "",
  );

  useEffect(() => {
    if (currentModel !== undefined && !currentModel.efforts.includes(effort)) {
      setEffort(currentModel.default_effort);
    }
  }, [modelId, currentModel, effort]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void onSubmit({
      default_model: modelId,
      reasoning_effort: effort,
    });
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <Label htmlFor="model">Default model</Label>
        <Select value={modelId} onValueChange={(v) => v && setModelId(v)}>
          <SelectTrigger id="model">
            <SelectValue placeholder="Pick a model" />
          </SelectTrigger>
          <SelectContent>
            {models.map((m) => (
              <SelectItem key={m.id} value={m.id}>
                {m.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="effort">Reasoning effort</Label>
        <Select value={effort} onValueChange={(v) => v && setEffort(v)}>
          <SelectTrigger id="effort">
            <SelectValue placeholder="Pick an effort level" />
          </SelectTrigger>
          <SelectContent>
            {currentModel?.efforts.map((e) => (
              <SelectItem key={e} value={e}>
                {e}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {error && <p className="text-destructive text-sm">{error}</p>}

      <div className="flex justify-end">
        <Button type="submit" disabled={saving || !modelId || !effort}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}
