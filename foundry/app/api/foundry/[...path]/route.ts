import { handleFoundryRequest } from "@/lib/api/foundry";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ path: string[] }> };

export async function GET(request: Request, context: RouteContext) {
  const { path } = await context.params;
  return handleFoundryRequest(request, "GET", path);
}
export async function POST(request: Request, context: RouteContext) {
  const { path } = await context.params;
  return handleFoundryRequest(request, "POST", path);
}
export async function PATCH(request: Request, context: RouteContext) {
  const { path } = await context.params;
  return handleFoundryRequest(request, "PATCH", path);
}
export async function DELETE(request: Request, context: RouteContext) {
  const { path } = await context.params;
  return handleFoundryRequest(request, "DELETE", path);
}
export async function PUT(request: Request, context: RouteContext) {
  const { path } = await context.params;
  return handleFoundryRequest(request, "PUT", path);
}
