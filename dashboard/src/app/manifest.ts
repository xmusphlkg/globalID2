import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "GIDS Dashboard",
    short_name: "GIDS",
    description: "Global infectious disease surveillance dashboard",
    start_url: "/",
    display: "standalone",
    background_color: "#f8fafc",
    theme_color: "#0f6b62",
    icons: [
      {
        src: "/icons/gids-192.png",
        sizes: "192x192",
        type: "image/png",
      },
      {
        src: "/icons/gids-512.png",
        sizes: "512x512",
        type: "image/png",
      },
    ],
  };
}
