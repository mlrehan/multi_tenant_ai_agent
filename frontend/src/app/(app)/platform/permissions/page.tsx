"use client";

import { useMemo } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageHeader } from "@/components/shared/page-header";
import { ErrorState, TableSkeleton } from "@/components/shared/states";
import { RiskBadge } from "@/components/shared/status-badge";
import { PermissionList } from "@/components/shared/permission-list";
import { usePlatformRolePermissions } from "@/features/platform/hooks";
import { usePlatformPermissionCatalog, usePlatformRoles } from "@/features/rbac/hooks";
import type { RiskLevel } from "@/lib/types";

const RISK_ORDER: RiskLevel[] = ["critical", "high", "medium", "low"];

export default function PlatformPermissionsPage() {
  const catalog = usePlatformPermissionCatalog();
  const roles = usePlatformRoles();
  const rolePermissions = usePlatformRolePermissions();

  const byRisk = useMemo(() => {
    const counts = new Map<RiskLevel, number>();
    for (const p of catalog.data ?? []) {
      counts.set(p.risk_level, (counts.get(p.risk_level) ?? 0) + 1);
    }
    return counts;
  }, [catalog.data]);

  return (
    <div>
      <PageHeader
        eyebrow="Platform"
        title="Permissions"
        description="The platform-scope permission catalog, and which role grants what. Platform and tenant permissions are stored in separate tables — a tenant role can never hold anything on this page."
      />

      {catalog.data && (
        <div className="mb-6 flex flex-wrap gap-3">
          {RISK_ORDER.filter((level) => byRisk.get(level)).map((level) => (
            <div
              key={level}
              className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2"
            >
              <RiskBadge level={level} />
              <span className="text-sm font-semibold tabular-nums">{byRisk.get(level)}</span>
            </div>
          ))}
        </div>
      )}

      <Tabs defaultValue="catalog">
        <TabsList>
          <TabsTrigger value="catalog">Catalog</TabsTrigger>
          <TabsTrigger value="by-role">By role</TabsTrigger>
        </TabsList>

        <TabsContent value="catalog" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>Every platform permission</CardTitle>
              <CardDescription>
                Grouped by resource. Risk level isn&apos;t decoration — an impersonated session has
                every <code className="font-mono text-xs">high</code> and{" "}
                <code className="font-mono text-xs">critical</code> permission stripped from it.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {catalog.isLoading && <TableSkeleton rows={6} columns={1} />}
              {catalog.error && (
                <ErrorState error={catalog.error} resource="the permission catalog" scope="platform" />
              )}
              {catalog.data && (
                <PermissionList
                  permissions={catalog.data}
                  emptyMessage="No platform permissions are defined on this deployment yet. Seed them with scripts/bootstrap_platform_admin.py or scripts/seed_demo_data.py."
                />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="by-role" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>What each role grants</CardTitle>
              <CardDescription>
                The role definition only. A user&apos;s effective set is resolved separately and can
                differ — see their row in Users.
              </CardDescription>
            </CardHeader>
            <CardContent className="p-0">
              {(roles.isLoading || rolePermissions.isLoading) && (
                <div className="p-6">
                  <TableSkeleton rows={4} columns={3} />
                </div>
              )}
              {rolePermissions.error && (
                <div className="p-6">
                  <ErrorState error={rolePermissions.error} resource="role permissions" scope="platform" />
                </div>
              )}
              {roles.data && rolePermissions.data && (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Role</TableHead>
                      <TableHead className="text-right">Rank</TableHead>
                      <TableHead>Grants</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {roles.data.map((role) => {
                      const codes = rolePermissions.data.by_role_code[role.code] ?? [];
                      return (
                        <TableRow key={role.id}>
                          <TableCell className="align-top">
                            <p className="font-medium">{role.name}</p>
                            <code className="font-mono text-xs text-muted-foreground">
                              {role.code}
                            </code>
                          </TableCell>
                          <TableCell className="text-right align-top tabular-nums">
                            {role.rank}
                          </TableCell>
                          <TableCell>
                            {codes.length === 0 ? (
                              <span className="text-sm text-muted-foreground">
                                No permissions attached
                              </span>
                            ) : (
                              <div className="flex flex-wrap gap-1.5">
                                {codes.map((code) => (
                                  <Badge key={code} variant="secondary" className="font-mono text-xs">
                                    {code}
                                  </Badge>
                                ))}
                              </div>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
