export type ToolButton = {
  label: string;
  action: string;
  metadata?: Record<string, unknown>;
};

export type ToolResponse<T = Record<string, unknown>> = {
  success: boolean;
  data: T;
  user_message: string;
  next_action?: string;
  buttons?: ToolButton[];
  error_code?: string;
  retryable?: boolean;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  buttons?: ToolButton[];
};

export type MenuItem = {
  product_id: string;
  name: string;
  description?: string;
  category: string;
  currency: string;
  available: boolean;
  price?: number;
  starting_price?: number;
  base_prices?: Record<string, number>;
  requires_customization?: boolean;
  customization_group_ids?: string[];
  upsell_group_ids?: string[];
  tags?: string[];
  metadata?: {
    display_reason?: string | null;
    serves?: string | null;
    is_popular?: boolean;
    recommendation_score?: number;
  };
};

export type OptionGroup = {
  option_group_id: string;
  name: string;
  type: "single_select" | "multi_select";
  required?: boolean;
  question: string;
  options: Array<{
    option_id: string;
    name?: string;
    label?: string;
    price_delta?: number;
    price_key?: string;
    available?: boolean;
  }>;
};

export type MenuOrderItem = {
  item_id: string;
  quantity: number;
  selected_options: Record<string, string | string[]>;
  label?: string;
};
