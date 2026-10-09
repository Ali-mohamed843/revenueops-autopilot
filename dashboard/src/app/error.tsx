"use client";

import { Button, Card } from "@/components/ui";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const unreachable = error.message.includes("not reachable");
  return (
    <Card className="mx-auto mt-6 w-full max-w-xl px-8 py-10 text-center">
      <p className="text-[13px] text-bad">{unreachable ? "Agent service offline" : "Something went wrong"}</p>
      <h1 className="serif mt-3 text-[30px] leading-tight font-normal">
        {unreachable ? "The agents can’t be reached" : "This page couldn’t load"}
      </h1>
      <p className="mt-3 text-sm text-ink-2">{error.message}</p>
      {unreachable && (
        <div className="mt-5 text-[13px] text-muted">
          <p>Start it from the agent-service folder:</p>
          <code className="mt-2 inline-block rounded-md bg-inset px-2.5 py-1.5 font-mono text-[12px] text-ink-2">
            uv run uvicorn revenueops.main:app --port 8000
          </code>
        </div>
      )}
      <Button className="mt-7" variant="primary" onClick={reset}>
        Try again
      </Button>
    </Card>
  );
}
