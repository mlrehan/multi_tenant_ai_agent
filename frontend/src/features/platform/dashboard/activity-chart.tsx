"use client";

import { format, parseISO } from "date-fns";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { DailyActivity } from "@/lib/types";

/** Two weeks of questions and new conversations, per UTC day.
 *
 *  Bars rather than a line: each day is a discrete count, and a line drawn
 *  between two quiet days implies a slope that never happened. */
export function ActivityChart({
  daily,
}: {
  daily: Pick<DailyActivity, "day" | "questions" | "conversations_started">[];
}) {
  const data = daily.map((d) => ({
    label: format(parseISO(d.day), "d MMM"),
    Questions: d.questions,
    "New conversations": d.conversations_started,
  }));

  return (
    <div className="h-64 w-full" role="img" aria-label="Questions and new conversations per day, last 14 days">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }} barGap={2}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis
            dataKey="label"
            tickLine={false}
            axisLine={false}
            fontSize={11}
            stroke="var(--muted-foreground)"
            interval="preserveStartEnd"
            minTickGap={12}
          />
          <YAxis
            allowDecimals={false}
            tickLine={false}
            axisLine={false}
            fontSize={11}
            stroke="var(--muted-foreground)"
          />
          <Tooltip
            cursor={{ fill: "var(--muted)", opacity: 0.5 }}
            contentStyle={{
              background: "var(--popover)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              fontSize: 12,
              color: "var(--popover-foreground)",
            }}
          />
          <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="Questions" fill="var(--chart-1)" radius={[3, 3, 0, 0]} maxBarSize={22} />
          <Bar dataKey="New conversations" fill="var(--chart-2)" radius={[3, 3, 0, 0]} maxBarSize={22} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
