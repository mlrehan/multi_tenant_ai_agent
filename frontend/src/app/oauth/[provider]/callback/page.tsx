"use client";

import { Suspense, useEffect, useState } from "react";
import { use as usePromise } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { XCircle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ShieldHalf } from "lucide-react";
import { completeOAuth } from "@/features/auth/api";
import { isApiError } from "@/lib/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { sessionQueryKey } from "@/features/auth/hooks";

function OAuthCallbackPageContent({
  params,
}: {
  params: Promise<{ provider: string }>;
}) {
  const { provider } = usePromise(params);
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const [exchangeError, setExchangeError] = useState<string | null>(null);

  const code = searchParams.get("code");
  const state = searchParams.get("state");
  // Derived during render rather than pushed into state by an effect --
  // it's a pure function of the URL, so there's nothing to synchronize.
  const error = !code || !state
    ? "This sign-in link is missing required parameters."
    : exchangeError;

  useEffect(() => {
    if (!code || !state) return;
    completeOAuth(provider, code, state)
      .then(() => {
        queryClient.invalidateQueries({ queryKey: sessionQueryKey });
        router.push("/select-tenant");
      })
      .catch((err) => {
        setExchangeError(
          isApiError(err) && err.status === 409
            ? "An account with this email already exists using a different sign-in method. Sign in that way instead."
            : "Sign-in didn't complete. Try again.",
        );
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, code, state]);

  if (error) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-8 bg-background px-4 py-12">
        <div className="flex items-center gap-2 text-foreground">
          <ShieldHalf className="size-6 text-primary" />
          <span className="text-lg font-semibold tracking-tight">IAM Control Center</span>
        </div>
        <Card className="w-full max-w-sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <XCircle className="size-5 text-status-danger" />
              Sign-in failed
            </CardTitle>
            <CardDescription>{error}</CardDescription>
          </CardHeader>
          <CardContent>
            <Link href="/login" className="text-sm text-primary hover:underline">
              Back to sign in
            </Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
      Completing sign-in…
    </div>
  );
}

export default function OAuthCallbackPage({
  params,
}: {
  params: Promise<{ provider: string }>;
}) {
  // useSearchParams() forces a client-side bailout during static prerender;
  // Next requires it to sit under a Suspense boundary or `next build` fails
  // on this route (caught by the production build -- `next dev` renders it
  // without complaint).
  return (
    <Suspense fallback={null}>
      <OAuthCallbackPageContent params={params} />
    </Suspense>
  );
}
