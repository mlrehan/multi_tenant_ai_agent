/**
 * The header every console screen opens with.
 *
 * `eyebrow` names the section above the title. On a console where "Users",
 * "Roles" and "Permissions" all exist at both platform and tenant scope, the
 * title alone is genuinely ambiguous — the eyebrow is what tells an operator
 * which set of data they're about to act on, which matters when the action is
 * "suspend".
 *
 * The description is capped near 75ch: full-bleed prose across a 1440px window
 * is unreadable, and these descriptions carry real operational caveats.
 */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-6 border-b border-border pb-5">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          {eyebrow && (
            <p className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
              {eyebrow}
            </p>
          )}
          <h1 className="text-2xl font-semibold tracking-tight text-balance">{title}</h1>
          {description && (
            <p className="mt-1.5 max-w-[75ch] text-sm leading-relaxed text-muted-foreground">
              {description}
            </p>
          )}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}
