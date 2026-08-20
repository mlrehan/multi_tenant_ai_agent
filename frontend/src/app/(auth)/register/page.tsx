"use client";

import { useState } from "react";
import Link from "next/link";
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
import { useRegister } from "@/features/auth/hooks";
import { isApiError } from "@/lib/api-client";

const schema = z.object({
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(8, "Use at least 8 characters."),
});
type FormValues = z.infer<typeof schema>;

export default function RegisterPage() {
  const register = useRegister();
  const [submitted, setSubmitted] = useState(false);
  const [violations, setViolations] = useState<string[]>([]);
  const [formError, setFormError] = useState<string | null>(null);

  const {
    register: registerField,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  async function onSubmit(values: FormValues) {
    setFormError(null);
    setViolations([]);
    try {
      await register.mutateAsync(values);
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
            Check your inbox
          </CardTitle>
          <CardDescription>
            If that email address is valid, we&apos;ve sent a verification link. Follow it to
            activate your account, then sign in.
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
        <CardTitle>Create an account</CardTitle>
        <CardDescription>Register to get started with the console.</CardDescription>
      </CardHeader>
      <CardContent>
        <form method="post" onSubmit={handleSubmit(onSubmit)} className="space-y-4">
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
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              autoFocus
              className="mt-1.5"
              {...registerField("email")}
            />
            <FieldError message={errors.email?.message} />
          </div>
          <div>
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              className="mt-1.5"
              {...registerField("password")}
            />
            <FieldError message={errors.password?.message} />
          </div>
          <Button type="submit" className="w-full" disabled={register.isPending}>
            {register.isPending ? "Creating account…" : "Create account"}
          </Button>
        </form>
        <p className="mt-4 text-center text-sm text-muted-foreground">
          Already have an account?{" "}
          <Link href="/login" className="text-primary hover:underline">
            Sign in
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
