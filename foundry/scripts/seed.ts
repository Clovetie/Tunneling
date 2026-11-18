import "dotenv/config";
import { query, isDatabaseConfigured } from "@/lib/db";
import { TEMPLATES } from "@/lib/templates";

async function main() {
  if (!isDatabaseConfigured()) throw new Error("DATABASE_URL is required");
  const ownerId = process.env.FOUNDRY_OWNER_ID;
  let inserted = 0;
  for (const template of TEMPLATES) {
    const rows = await query(
      `insert into public.agent_templates (owner_id,slug,name,description,category,is_roblox,config)
       values (null,$1,$2,$3,$4,$5,$6::jsonb)
       on conflict ((coalesce(owner_id, '00000000-0000-0000-0000-000000000000'::uuid)), slug) do update
       set name=excluded.name,description=excluded.description,category=excluded.category,is_roblox=excluded.is_roblox,config=excluded.config
       returning id`,
      [template.slug, template.name, template.description, template.category, template.isRoblox, JSON.stringify(template.config)]
    );
    if (rows.length) inserted++;
  }
  if (ownerId) {
    await query(
      `insert into public.projects (owner_id,name,slug,description,metadata)
       values ($1,'Personal Workspace','personal-workspace','General-purpose tasks, notes, prompts and saved outputs.',$2::jsonb)
       on conflict (owner_id,slug) do nothing`,
      [ownerId, JSON.stringify({ kind: "personal", source: "seed" })]
    );
    for (const template of TEMPLATES.filter((item) => item.category === "general").slice(0, 4)) {
      const config = template.config;
      await query(
        `insert into public.agents
          (owner_id,name,slug,description,avatar,identity,system_instructions,model,generation,tool_permissions,memory_config,knowledge_sources,communication_permissions,execution_limits,assigned_projects,status)
         values ($1,$2,$3,$4,$5,$6::jsonb,$7,$8::jsonb,$9::jsonb,$10::jsonb,$11::jsonb,$12::jsonb,$13::jsonb,$14::jsonb,$15::jsonb,'idle')
         on conflict (owner_id,slug) do nothing`,
        [
          ownerId, template.name, template.slug, template.description, config.avatar ?? "bot",
          JSON.stringify(config.identity ?? {}), config.systemInstructions ?? "", JSON.stringify(config.model ?? {}),
          JSON.stringify(config.generation ?? {}), JSON.stringify(config.toolPermissions ?? []), JSON.stringify(config.memoryConfig ?? {}),
          JSON.stringify(config.knowledgeSources ?? []), JSON.stringify(config.communicationPermissions ?? {}),
          JSON.stringify(config.executionLimits ?? {}), JSON.stringify([])
        ]
      );
    }
  }
  console.log(`[foundry:seed] ${inserted} templates; ownerId=${ownerId ?? "(not set; templates only)"}`);
}

void main().catch((error) => { console.error("[foundry:seed] failed", error instanceof Error ? error.message : String(error)); process.exit(1); });
