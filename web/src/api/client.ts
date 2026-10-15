import type {
  ObjectItem,
  ObjectsListResponse,
  StickerDetail,
  StickerListResponse,
  CategoryTree,
  DesignData,
  DesignValidationResult,
  StickerImageSize,
} from "../types";

export interface StickerFilterParams {
  q?: string;
  category?: string;
  topic?: string;
  tag?: string;
  page?: number;
  per_page?: number;
}

export interface PopularTagItem {
  tag: string;
  count: number;
}

export interface PopularTagsResponse {
  tags: PopularTagItem[];
  total_unique: number;
}

class ApiClient {
  private baseUrl = "";

  setBaseUrl(url: string) {
    this.baseUrl = url.replace(/\/$/, "");
  }

  private async request<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const url = `${this.baseUrl}${endpoint}`;
    const res = await fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
    });

    if (!res.ok) {
      let errorMsg = `API request failed with status ${res.status}`;
      try {
        const errJson = await res.json();
        if (errJson.detail) {
          errorMsg = typeof errJson.detail === "string" ? errJson.detail : JSON.stringify(errJson.detail);
        }
      } catch {
        // use default error message
      }
      throw new Error(errorMsg);
    }

    return res.json() as Promise<T>;
  }

  async getObjects(): Promise<ObjectItem[]> {
    const res = await this.request<ObjectsListResponse>("/api/v1/objects");
    return res.items ?? [];
  }

  async getObject(objectId: string): Promise<ObjectItem> {
    return this.request<ObjectItem>(`/api/v1/objects/${encodeURIComponent(objectId)}`);
  }

  async getStickers(params: StickerFilterParams = {}): Promise<StickerListResponse> {
    const searchParams = new URLSearchParams();
    if (params.q) searchParams.set("q", params.q);
    if (params.category) searchParams.set("category", params.category);
    if (params.topic) searchParams.set("topic", params.topic);
    if (params.tag) searchParams.set("tag", params.tag);
    if (params.page !== undefined) searchParams.set("page", params.page.toString());
    if (params.per_page !== undefined) searchParams.set("per_page", params.per_page.toString());

    const qs = searchParams.toString();
    const endpoint = `/api/v1/stickers${qs ? `?${qs}` : ""}`;
    return this.request<StickerListResponse>(endpoint);
  }

  async getStickerDetail(assetId: string): Promise<StickerDetail> {
    return this.request<StickerDetail>(`/api/v1/stickers/${encodeURIComponent(assetId)}`);
  }

  async getCategories(): Promise<CategoryTree> {
    return this.request<CategoryTree>("/api/v1/categories");
  }

  async getPopularTags(limit = 30): Promise<PopularTagsResponse> {
    return this.request<PopularTagsResponse>(`/api/v1/tags?limit=${limit}`);
  }

  async validateDesign(design: DesignData): Promise<DesignValidationResult> {
    return this.request<DesignValidationResult>("/api/v1/design/validate", {
      method: "POST",
      body: JSON.stringify(design),
    });
  }

  getStickerImageUrl(assetId: string, size: StickerImageSize = "thumb"): string {
    return `${this.baseUrl}/api/v1/stickers/${encodeURIComponent(assetId)}/image?size=${size}`;
  }
}

export const api = new ApiClient();
