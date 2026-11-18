"use client";

import { FlowBuilder } from "@/components/builder/flow-builder";
import { SectionHeader } from "@/components/ui";

export default function BuilderPage() {
  return <div><SectionHeader eyebrow="Visual builder" title="Agent Builder" description="Compose conditional, parallel and sequential workflows with durable tasks, retry policies, resource locks, approval gates and live execution highlighting." /><FlowBuilder /></div>;
}
