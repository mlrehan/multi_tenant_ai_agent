"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { FieldError } from "@/components/shared/field-error";
import { useConfirmPasswordReset } from "@/features/auth/hooks";
import { isApiError } from "@/lib/api-client";

const schema = z.object({ password: z.string().min(8, "Use at least 8 characters.") });
type FormValues = z.infer<typeof schema>;

function ResetPasswordPageContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const confirmReset = useConfirmPasswordReset();
  const [submitted, setSubmitted] = useState(false);
  const [violations, setViolations] = useState<string[]>([]);
  const [formError, setFormError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  async function onSubmit(values: FormValues) {
    if (!token) {
      setFormError("This reset link is missing its token.");
      return;
    }
    setFormError(null);
    setViolations([]);
    try {
      await confirmReset.mutateAsync({ token, password: values.password });
      setSubmitted(true);
    } catch (error) {
      if (isApiError(error) && error.violations) {
        setViolations(error.violations);
      } else if (isApiError(error)) {
        setFormError(error.message);
      } else {
        setFormError("Something went wrong. Try again.");
      }
    }
  }

  if (submitted) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CheckCircle2 className="size-5 text-status-success" />
            Password reset
          </CardTitle>
          <CardDescription>
            Your password has been changed and every other session has been signed out. Sign in
            with your new password.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Link href="/login" className="text-sm text-primary hover:underline">
            Back to sign in
          </Link>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Set a new password</CardTitle>
        <CardDescription>Choose a new password for your account.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          {formError && (
            <Alert variant="destructive">
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          )}
          {violations.length > 0 && (
            <Alert variant="destructive">
              <AlertDescription>
                <p className="mb-1 font-medium">Choose a stronger password:</p>
                <ul className="list-disc space-y-0.5 pl-4">
                  {violations.map((v) => (
                    <li key={v}>{v}</li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          )}
          <div>
            <Label htmlFor="password">New password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              autoFocus
              className="mt-1.5"
              {...register("password")}
            />
            <FieldError message={errors.password?.message} />
          </div>
          <Button type="submit" className="w-full" disabled={confirmReset.isPending}>
            {confirmReset.isPending ? "Resetting…" : "Reset password"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

export default function ResetPasswordPage() {
  // useSearchParams() forces a client-side bailout during static prerender;
  // Next requires it to sit under a Suspense boundary or `next build` fails
  // on this route (caught by the production build -- `next dev` renders it
  // without complaint).
  return (
    <Suspense fallback={null}>
      <ResetPasswordPageContent />
    </Suspense>
  );
}
