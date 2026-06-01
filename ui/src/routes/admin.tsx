import { Navigate, createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import type {Profile, ProfileUpdate} from "@/lib/api";
import { AppHeader } from "@/components/AppHeader";
import { ProfileForm } from "@/components/ProfileForm";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {   api } from "@/lib/api";
import { useSession } from "@/lib/session";

export const Route = createFileRoute("/admin")({ component: AdminPage });

function AdminPage() {
  const session = useSession();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const options = useQuery({
    queryKey: ["options"],
    queryFn: api.options,
    enabled: !!session.data?.is_admin,
  });

  const profiles = useQuery({
    queryKey: ["adminProfiles"],
    queryFn: api.adminListProfiles,
    enabled: !!session.data?.is_admin,
  });

  const save = useMutation({
    mutationFn: ({ id, body }: { id: string; body: ProfileUpdate }) =>
      api.adminSaveProfile(id, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["adminProfiles"] });
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
  if (!session.data.is_admin) return <Navigate to="/profile" />;

  const activeProfile: Profile | null =
    (selected && profiles.data?.find((p) => p.id === selected)) || null;

  return (
    <div className="min-h-svh">
      <AppHeader user={session.data} />
      <main className="container mx-auto grid grid-cols-1 gap-6 p-6 md:grid-cols-[280px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>All users</CardTitle>
            <CardDescription>{profiles.data?.length ?? 0} profiles</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-1">
            {profiles.isLoading ? (
              <Skeleton className="h-32" />
            ) : (
              profiles.data?.map((p) => (
                <Button
                  key={p.id}
                  variant={selected === p.id ? "secondary" : "ghost"}
                  className="justify-start"
                  onClick={() => setSelected(p.id ?? null)}
                >
                  <span className="truncate">{p.id}</span>
                </Button>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{activeProfile?.id ?? "Select a user"}</CardTitle>
            <CardDescription>{activeProfile?.email ?? ""}</CardDescription>
          </CardHeader>
          <CardContent>
            {!activeProfile ? (
              <p className="text-muted-foreground text-sm">Pick a user on the left to edit.</p>
            ) : options.isLoading ? (
              <Skeleton className="h-48" />
            ) : (
              <ProfileForm
                models={options.data?.models ?? []}
                initial={activeProfile}
                onSubmit={(body) =>
                  save.mutateAsync({ id: activeProfile.id!, body })
                }
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
