import { query, transaction } from "@/lib/db";
import { assertProjectAccess } from "@/lib/access";
import { embedText, toVectorLiteral, embeddingStatus } from "@/lib/embeddings";
import { ApiError } from "@/lib/errors";

export function chunkText(text: string, size = 1200, overlap = 150): string[] {
  const clean = text.replace(/\r\n/g, "\n").trim();
  if (!clean) return [];
  if (clean.length <= size) return [clean];
  const chunks: string[] = [];
  let start = 0;
  while (start < clean.length && chunks.length < 60) {
    let end = Math.min(clean.length, start + size);
    if (end < clean.length) {
      const boundary = clean.lastIndexOf("\n", end);
      if (boundary > start + Math.floor(size * 0.6)) end = boundary;
    }
    chunks.push(clean.slice(start, end).trim());
    if (end >= clean.length) break;
    start = Math.max(start + 1, end - overlap);
  }
  return chunks.filter(Boolean);
}

export async function listKnowledgeDocuments(ownerId: string, projectId?: string | null) {
  if (projectId) await assertProjectAccess(ownerId, projectId, "viewer");
  return query(
    `select id, project_id, title, source_uri, mime_type, metadata, length(content) as content_length, created_at, updated_at
     from public.knowledge_documents
     where owner_id=$1 and ($2::uuid is null or project_id=$2)
     order by updated_at desc limit 200`,
    [ownerId, projectId ?? null]
  );
}

export async function createKnowledgeDocument(input: {
  ownerId: string;
  projectId?: string | null;
  title: string;
  sourceUri?: string | null;
  mimeType?: string;
  content: string;
  metadata?: Record<string, unknown>;
}) {
  if (input.projectId) await assertProjectAccess(input.ownerId, input.projectId, "editor");
  const chunks = chunkText(input.content);
  const embeddings = await Promise.all(chunks.map((chunk) => embedText(chunk)));
  const modes = [...new Set(embeddings.map((item) => item.mode))];
  return transaction(async (client) => {
    const docs = await client.query<{ id: string }>(
      `insert into public.knowledge_documents (owner_id, project_id, title, source_uri, mime_type, metadata, content)
       values ($1,$2,$3,$4,$5,$6::jsonb,$7) returning id`,
      [
        input.ownerId, input.projectId ?? null, input.title, input.sourceUri ?? null, input.mimeType ?? "text/plain",
        JSON.stringify({ ...(input.metadata ?? {}), embeddingModes: modes }), input.content
      ]
    );
    const id = docs.rows[0].id;
    for (let index = 0; index < chunks.length; index++) {
      await client.query(
        `insert into public.knowledge_chunks (owner_id, document_id, project_id, chunk_index, content, embedding, metadata)
         values ($1,$2,$3,$4,$5,$6::vector,$7::jsonb)`,
        [
          input.ownerId, id, input.projectId ?? null, index, chunks[index],
          embeddings[index].vector ? toVectorLiteral(embeddings[index].vector!) : null,
          JSON.stringify({ embeddingMode: embeddings[index].mode, embeddingModel: embeddings[index].model, reason: embeddings[index].reason ?? null })
        ]
      );
    }
    return { id, chunks: chunks.length, embeddingModes: modes };
  });
}

export async function deleteKnowledgeDocument(ownerId: string, documentId: string) {
  const rows = await query("delete from public.knowledge_documents where id=$1 and owner_id=$2 returning id", [ownerId, documentId]);
  return { deleted: rows.length > 0 };
}

export async function searchKnowledge(ownerId: string, input: { query: string; projectId?: string | null; documentId?: string | null; limit?: number }) {
  let projectId = input.projectId ?? null;
  let documentId = input.documentId ?? null;
  if (documentId) {
    const docs = await query<{ id: string; project_id: string | null }>(
      "select id, project_id from public.knowledge_documents where id=$1 and owner_id=$2 limit 1",
      [documentId, ownerId]
    );
    const doc = docs[0];
    if (!doc) throw new ApiError("Knowledge document not found.", 404, "NOT_FOUND");
    if (projectId && doc.project_id && projectId !== doc.project_id) throw new ApiError("Knowledge document belongs to another project.", 403, "PROJECT_ISOLATION_DENIED");
    if (!projectId) projectId = doc.project_id;
    await assertProjectAccess(ownerId, projectId, "viewer");
  } else if (projectId) {
    await assertProjectAccess(ownerId, projectId, "viewer");
  }
  const limit = Math.min(Math.max(input.limit ?? 8, 1), 30);
  const embedding = await embedText(input.query);
  if (embedding.vector) {
    const rows = await query<Record<string, unknown>>(
      `select * from public.match_knowledge_chunks($1::vector,$2,$3,$4,$5)
       where ($4::uuid is not null or project_id is null)`,
      [toVectorLiteral(embedding.vector), limit, ownerId, projectId, documentId]
    );
    return {
      mode: "vector",
      embedding: { provider: embedding.provider, model: embedding.model },
      results: rows.map((row) => ({ ...row, citation: { type: "knowledge", documentId: String(row.document_id), title: String(row.title), chunkIndex: Number(row.chunk_index), similarity: Number(row.similarity) } }))
    };
  }
  const params: unknown[] = [ownerId];
  const clauses = ["kc.owner_id = $1"];
  if (projectId) { params.push(projectId); clauses.push(`kc.project_id = $${params.length}`); }
  else clauses.push("kc.project_id is null");
  if (documentId) { params.push(documentId); clauses.push(`kc.document_id = $${params.length}`); }
  const like = `%${input.query}%`;
  params.push(like); const likeIndex = params.length;
  params.push(limit); const limitIndex = params.length;
  const rows = await query<Record<string, unknown>>(
    `select kc.id, kc.document_id, kc.project_id, kd.title, kc.chunk_index, kc.content, 0.58 as similarity, kc.metadata
     from public.knowledge_chunks kc join public.knowledge_documents kd on kd.id=kc.document_id
     where ${clauses.join(" and ")} and kc.content ilike $${likeIndex}
     order by kc.chunk_index asc limit $${limitIndex}`,
    params
  );
  return {
    mode: "lexical",
    embedding: { ...embeddingStatus(), reason: embedding.reason },
    results: rows.map((row) => ({ ...row, citation: { type: "knowledge", documentId: String(row.document_id), title: String(row.title), chunkIndex: Number(row.chunk_index), similarity: Number(row.similarity) } }))
  };
}
