export const branchId = process.env.NEXT_PUBLIC_BRANCH_ID;

if (!branchId) {
  throw new Error("NEXT_PUBLIC_BRANCH_ID is required");
}
