import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import type { ProfileUpdate } from "@/lib/api";
import { AppHeader } from "@/components/AppHeader";
import { ProfileForm } from "@/components/ProfileForm";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/profile")({ component: ProfilePage });

function ProfilePage() {
  const session = useSession();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const options = useQuery({
    queryKey: ["options"],
    queryFn: api.options,
    enabled: !!session.data,
  });

  const profile = useQuery({
    queryKey: ["profile"],
    queryFn: api.profile,
    enabled: !!session.data,
  });

  const save = useMutation({
    mutationFn: (body: ProfileUpdate) => api.saveProfile(body),
    onSuccess: (saved) => {
      qc.setQueryData(["profile"], saved);
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  if (session.isLoading) {
    return (
      <main className="container mx-auto p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }

  if (!session.data) return <Navigate to="/login" />;

  return (
    <div className="min-h-svh">
      <AppHeader user={session.data} />
      <main className="container mx-auto p-6">
        <Card className="mx-auto max-w-2xl">
          <CardHeader>
            <CardTitle>Your agent-framework profile</CardTitle>
            <CardDescription>
              These defaults are applied when you invoke the agent.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {options.isLoading || profile.isLoading ? (
              <Skeleton className="h-48 w-full" />
            ) : (
              <ProfileForm
                models={options.data?.models ?? []}
                initial={profile.data ?? ({})}
                onSubmit={(body) => save.mutateAsync(body)}
                saving={save.isPending}
                error={error}
              />
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
