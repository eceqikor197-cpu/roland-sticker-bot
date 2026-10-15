import { useState, useEffect } from "react";

const imageCache = new Map<string, HTMLImageElement>();

export function useImageLoader(url: string | null): [HTMLImageElement | null, "loading" | "loaded" | "failed"] {
  const [image, setImage] = useState<HTMLImageElement | null>(() => {
    if (!url) return null;
    return imageCache.get(url) ?? null;
  });
  const [status, setStatus] = useState<"loading" | "loaded" | "failed">(() => {
    if (!url) return "failed";
    return imageCache.has(url) ? "loaded" : "loading";
  });

  useEffect(() => {
    if (!url) {
      setImage(null);
      setStatus("failed");
      return;
    }

    if (imageCache.has(url)) {
      setImage(imageCache.get(url)!);
      setStatus("loaded");
      return;
    }

    setStatus("loading");
    const img = new Image();
    img.crossOrigin = "anonymous";

    let isMounted = true;

    img.onload = () => {
      imageCache.set(url, img);
      if (isMounted) {
        setImage(img);
        setStatus("loaded");
      }
    };

    img.onerror = () => {
      if (isMounted) {
        setStatus("failed");
      }
    };

    img.src = url;

    return () => {
      isMounted = false;
    };
  }, [url]);

  return [image, status];
}
