import { ShieldHalf } from "lucide-react";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-8 bg-background px-4 py-12">
      <div className="flex items-center gap-2 text-foreground">
        <ShieldHalf className="size-6 text-primary" />
        <span className="text-lg font-semibold tracking-tight">IAM Control Center</span>
      </div>
      <div className="w-full max-w-sm">{children}</div>
    </div>
  );
}
