export type MenuItem = {
  product_id: string;
  name: string;
  category: string;
  currency: string;
  description?: string;
  available: boolean;
  archived?: boolean;
  price?: number;
  starting_price?: number;
  base_prices?: Record<string, number>;
  requires_customization?: boolean;
  customization_group_ids?: string[];
  upsell_group_ids?: string[];
  tags?: string[];
  search_terms?: string[];
  image_url?: string | null;
  metadata?: Record<string, unknown>;
};
