"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { FieldError } from "@/components/shared/field-error";
import { useLogin, useVerifyMfa } from "@/features/auth/hooks";
import { startOAuth } from "@/features/auth/api";
import { isApiError } from "@/lib/api-client";

const loginSchema = z.object({
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
});
type LoginForm = z.infer<typeof loginSchema>;

const mfaSchema = z.object({
  code: z.string().min(6, "Enter the 6-digit code.").max(6, "Enter the 6-digit code."),
});
type MfaForm = z.infer<typeof mfaSchema>;

function LoginPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const login = useLogin();
  const verifyMfa = useVerifyMfa();
  const [challengeId, setChallengeId] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginForm>({ resolver: zodResolver(loginSchema) });

  const {
    register: registerMfa,
    handleSubmit: handleMfaSubmit,
    formState: { errors: mfaErrors },
  } = useForm<MfaForm>({ resolver: zodResolver(mfaSchema) });

  function goToDestination() {
    router.push(searchParams.get("next") ?? "/select-tenant");
  }

  async function onSubmit(values: LoginForm) {
    setFormError(null);
    try {
      const result = await login.mutateAsync(values);
      if (result.status === "mfa_required" && result.mfa_challenge_id) {
        setChallengeId(result.mfa_challenge_id);
        return;
      }
      goToDestination();
    } catch (error) {
      if (isApiError(error)) {
        if (error.status === 423) {
          setFormError("This account is temporarily locked after too many failed attempts. Try again later.");
        } else if (error.status === 429) {
          setFormError("Too many attempts. Wait a few minutes before trying again.");
        } else if (error.status === 401) {
          setFormError("Incorrect email or password.");
        } else {
          setFormError(error.message);
        }
      } else {
        setFormError("Something went wrong. Try again.");
      }
    }
  }

  async function onSubmitMfa(values: MfaForm) {
    if (!challengeId) return;
    setFormError(null);
    try {
      await verifyMfa.mutateAsync({ challengeId, code: values.code });
      goToDestination();
    } catch (error) {
      setFormError(isApiError(error) ? "That code didn't work. Try again." : "Something went wrong.");
    }
  }

  if (challengeId) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Enter your verification code</CardTitle>
          <CardDescription>Open your authenticator app and enter the 6-digit code.</CardDescription>
        </CardHeader>
        <CardContent>
          <form method="post" onSubmit={handleMfaSubmit(onSubmitMfa)} className="space-y-4">
            {formError && (
              <Alert variant="destructive">
                <AlertDescription>{formError}</AlertDescription>
              </Alert>
            )}
            <div>
              <Label htmlFor="code">Verification code</Label>
              <Input
                id="code"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                autoFocus
                className="mt-1.5 tracking-[0.3em]"
                {...registerMfa("code")}
              />
              <FieldError message={mfaErrors.code?.message} />
            </div>
            <Button type="submit" className="w-full" disabled={verifyMfa.isPending}>
              {verifyMfa.isPending ? "Verifying…" : "Verify"}
            </Button>
          </form>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sign in</CardTitle>
        <CardDescription>Enter your credentials to access the console.</CardDescription>
      </CardHeader>
      <CardContent>
        <form method="post" onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          {formError && (
            <Alert variant="destructive">
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          )}
          <div>
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              autoFocus
              className="mt-1.5"
              {...register("email")}
            />
            <FieldError message={errors.email?.message} />
          </div>
          <div>
            <div className="flex items-center justify-between">
              <Label htmlFor="password">Password</Label>
              <Link href="/forgot-password" className="text-xs text-primary hover:underline">
                Forgot password?
              </Link>
            </div>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              className="mt-1.5"
              {...register("password")}
            />
            <FieldError message={errors.password?.message} />
          </div>
          <Button type="submit" className="w-full" disabled={login.isPending}>
            {login.isPending ? "Signing in…" : "Sign in"}
          </Button>
        </form>

        <div className="my-4 flex items-center gap-3">
          <div className="h-px flex-1 bg-border" />
          <span className="text-xs text-muted-foreground">or continue with</span>
          <div className="h-px flex-1 bg-border" />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <OAuthButton provider="google" label="Google" />
          <OAuthButton provider="facebook" label="Facebook" />
        </div>

        <p className="mt-4 text-center text-sm text-muted-foreground">
          Don&apos;t have an account?{" "}
          <Link href="/register" className="text-primary hover:underline">
            Create one
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}

function OAuthButton({ provider, label }: { provider: "google" | "facebook"; label: string }) {
  const [pending, setPending] = useState(false);

  async function handleClick() {
    setPending(true);
    try {
      const { authorization_url } = await startOAuth(provider);
      window.location.href = authorization_url;
    } catch {
      // Most likely the provider isn't configured in this environment
      // (OAUTH_GOOGLE__ENABLED=false etc.) -- nothing actionable for the
      // user beyond trying email/password instead.
      setPending(false);
    }
  }

  return (
    <Button type="button" variant="outline" size="sm" disabled={pending} onClick={handleClick}>
      {label}
    </Button>
  );
}

export default function LoginPage() {
  // useSearchParams() forces a client-side bailout during static prerender;
  // Next requires it to sit under a Suspense boundary or `next build` fails
  // on this route (caught by the production build -- `next dev` renders it
  // without complaint).
  return (
    <Suspense fallback={null}>
      <LoginPageContent />
    </Suspense>
  );
}
