import AIModelsView from "@/features/ai/models/view";

// The model editor changes with each dashboard deployment. Serve fresh HTML
// so clients do not keep an old form while the API already uses a newer schema.
export const dynamic = "force-dynamic";

export default AIModelsView;
