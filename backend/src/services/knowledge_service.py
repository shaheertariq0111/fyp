from src.models.tool_responses import ToolResponse


class KnowledgeService:
    def __init__(self, client, knowledge_base_id: str, max_results: int):
        self.client = client
        self.knowledge_base_id = knowledge_base_id
        self.max_results = max_results

    def retrieve(self, question: str, branch_id=None, language="en") -> ToolResponse:
        if not self.knowledge_base_id:
            return ToolResponse.error(
                error_code="KNOWLEDGE_BASE_UNAVAILABLE",
                user_message="I can't verify that restaurant information right now.",
                retryable=True,
            )
        query = question
        if branch_id:
            query = f"Branch: {branch_id}\nLanguage: {language}\nQuestion: {question}"
        try:
            response = self.client.retrieve(
                knowledgeBaseId=self.knowledge_base_id,
                retrievalQuery={"text": query},
                retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": self.max_results}},
            )
        except Exception:
            return ToolResponse.error(error_code="KNOWLEDGE_BASE_TIMEOUT",
                                      user_message="I can't verify that information right now.", retryable=True)
        results = [{"text": result.get("content", {}).get("text", ""),
                    "score": result.get("score"), "location": result.get("location")}
                   for result in response.get("retrievalResults", [])]
        return ToolResponse.ok(data={"results": results},
                               user_message="I found restaurant information from the approved knowledge source.",
                               next_action="answer_from_knowledge")
