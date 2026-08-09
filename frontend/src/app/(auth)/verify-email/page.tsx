"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { CheckCircle2, XCircle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { verifyEmail } from "@/features/auth/api";
import { isApiError } from "@/lib/api-client";

type Status = "checking" | "success" | "error";

function VerifyEmailPageContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const [result, setResult] = useState<{ status: Status; message: string } | null>(null);

  // The missing-token case is a pure function of the URL, so it's derived
  // during render; only the async verification result goes through state.
  const { status, message }: { status: Status; message: string } = !token
    ? { status: "error", message: "This verification link is missing its token." }
    : (result ?? { status: "checking", message: "" });

  useEffect(() => {
    if (!token) return;
    verifyEmail(token)
      .then(() => setResult({ status: "success", message: "" }))
      .catch((error) => {
        setResult({
          status: "error",
          message: isApiError(error) ? error.message : "This link is invalid or has expired.",
        });
      });
  }, [token]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {status === "success" && <CheckCircle2 className="size-5 text-status-success" />}
          {status === "error" && <XCircle className="size-5 text-status-danger" />}
          {status === "checking" && "Verifying your email…"}
          {status === "success" && "Email verified"}
          {status === "error" && "Verification failed"}
        </CardTitle>
        <CardDescription>
          {status === "success" && "Your email is confirmed. You can now sign in."}
          {status === "error" && message}
        </CardDescription>
      </CardHeader>
      {status !== "checking" && (
        <CardContent>
          <Link href="/login" className="text-sm text-primary hover:underline">
            Back to sign in
          </Link>
        </CardContent>
      )}
    </Card>
  );
}

export default function VerifyEmailPage() {
  // useSearchParams() forces a client-side bailout during static prerender;
  // Next requires it to sit under a Suspense boundary or `next build` fails
  // on this route (caught by the production build -- `next dev` renders it
  // without complaint).
  return (
    <Suspense fallback={null}>
      <VerifyEmailPageContent />
    </Suspense>
  );
}
