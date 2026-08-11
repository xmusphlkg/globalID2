import createClient from "openapi-fetch";
import type { paths } from "./api";

/** Generated-contract client for same-origin control-plane feature modules. */
export const controlPlaneClient = createClient<paths>();
