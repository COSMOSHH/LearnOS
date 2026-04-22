import openai


class LLMGenerator:
    def __init__(self, model_name: str = "gpt-3.5-turbo", api_key: str = None, base_url: str = None):
        self.model_name = model_name
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)

    def chat_once(self, messages: list, temperature: float = 0.0, max_tokens: int = 800) -> str:
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (response.choices[0].message.content or "").strip()

    def generate_answer(self, query: str, retrieved_docs: list, history: list | None = None, extra_system_context: str = "") -> str:
        if not retrieved_docs:
            return "I could not find relevant study material for this question."

        context_texts = [f"Chunk {index + 1}:\n{doc['document']}" for index, doc in enumerate(retrieved_docs)]
        combined_context = "\n\n".join(context_texts)
        system_prompt = (
            "You are a study assistant. Answer using the retrieved study material first. "
            "If the answer is not supported by the retrieved context, clearly say so.\n"
            f"Retrieved context:\n{combined_context}\n"
        )
        if extra_system_context:
            system_prompt += f"\nAdditional guidance:\n{extra_system_context}\n"

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": query})

        try:
            return self.chat_once(messages=messages, temperature=0.1, max_tokens=900)
        except Exception as exc:
            return f"Answer generation failed: {exc}"

    def generate_answer_stream(
        self,
        query: str,
        retrieved_results: list,
        history: list = None,
        extra_system_context: str = "",
    ):
        history = history or []
        context_list = []
        for item in retrieved_results:
            text = item.get("document") or item.get("content") or item.get("text") or item.get("page_content") or str(item)
            context_list.append(text)

        context = "\n\n".join(context_list)
        system_prompt = (
            "You are a study assistant. Answer the learner's question using the retrieved study material first. "
            "Keep the answer concise, accurate, and easy to review.\n"
            f"Retrieved study context:\n{context}\n"
        )
        if extra_system_context:
            system_prompt += f"\nAdditional guidance:\n{extra_system_context}\n"

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": query})

        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            stream=True,
            temperature=0.1,
        )

        for chunk in response:
            if chunk.choices[0].delta.content is not None:
                yield chunk.choices[0].delta.content

    def chat_with_tools_stream(self, messages: list, tools: list = None):
        kwargs = {"model": self.model_name, "messages": messages, "temperature": 0.0, "stream": True}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = self.client.chat.completions.create(**kwargs)
            content = ""
            tool_calls = {}

            for chunk in response:
                delta = chunk.choices[0].delta

                if getattr(delta, "content", None):
                    content += delta.content
                    yield {"type": "content", "data": delta.content}

                if getattr(delta, "tool_calls", None):
                    for tool_call in delta.tool_calls:
                        index = tool_call.index
                        if index not in tool_calls:
                            tool_calls[index] = {
                                "id": getattr(tool_call, "id", f"call_{index}"),
                                "type": "function",
                                "function": {
                                    "name": getattr(tool_call.function, "name", "") or "",
                                    "arguments": "",
                                },
                            }
                        else:
                            if getattr(tool_call.function, "name", None):
                                tool_calls[index]["function"]["name"] += tool_call.function.name
                            if getattr(tool_call.function, "arguments", None):
                                tool_calls[index]["function"]["arguments"] += tool_call.function.arguments

            yield {"type": "done", "content": content, "tool_calls": list(tool_calls.values()) if tool_calls else None}
        except Exception as exc:
            raise RuntimeError(f"LLM streaming with tools failed: {exc}")

    def chat_with_tools(self, messages: list, tools: list = None) -> dict:
        kwargs = {"model": self.model_name, "messages": messages, "temperature": 0.0}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = self.client.chat.completions.create(**kwargs)
            message = response.choices[0].message
            return {
                "message_obj": message,
                "content": message.content,
                "tool_calls": getattr(message, "tool_calls", None),
            }
        except Exception as exc:
            raise RuntimeError(f"LLM call with tools failed: {exc}")
