# Copyright © 2024 Pathway
"""
Pathway embedder UDFs.
"""
import asyncio

import numpy as np

import pathway as pw
from pathway.internals import udfs
from pathway.optional_import import optional_imports
from pathway.xpacks.llm._utils import _coerce_sync

__all__ = [
    "OpenAIEmbedder",
    "LiteLLMEmbedder",
    "SentenceTransformerEmbedder",
    "GeminiEmbedder",
]


async def _safe_aclose(self):
    try:
        await self.aclose()
    except RuntimeError:
        pass


def _monkeypatch_openai_async():
    """Be more permissive on errors happening in httpx loop closing.


    Without this patch, many runtime errors appear while the server is running in a thread.
    The errors can be ignored, but look scary.
    """
    try:
        with optional_imports("xpack-llm"):
            import openai._base_client

        if hasattr(openai._base_client, "OrigAsyncHttpxClientWrapper"):
            return

        openai._base_client.OrigAsyncHttpxClientWrapper = (  # type:ignore
            openai._base_client.AsyncHttpxClientWrapper
        )

        class AsyncHttpxClientWrapper(
            openai._base_client.OrigAsyncHttpxClientWrapper  # type:ignore
        ):
            def __del__(self) -> None:
                try:
                    # TODO(someday): support non asyncio runtimes here
                    asyncio.get_running_loop().create_task(_safe_aclose(self))
                except Exception:
                    pass

        openai._base_client.AsyncHttpxClientWrapper = (  # type:ignore
            AsyncHttpxClientWrapper
        )
    except Exception:
        pass


class BaseEmbedder(pw.UDF):
    def get_embedding_dimension(self, **kwargs):
        """Computes number of embedder's dimensions by asking the embedder to embed ``"."``.

        Args:
            **kwargs: parameters of the embedder, if unset defaults from the constructor
              will be taken.
        """
        return len(_coerce_sync(self.__wrapped__)(".", **kwargs))

    def __call__(
        self, input: pw.ColumnExpression, *args, **kwargs
    ) -> pw.ColumnExpression:
        """Embeds texts in a Column.

        Args:
            input (ColumnExpression[str]): Column with texts to embed
        """
        return super().__call__(input, *args, **kwargs)


class OpenAIEmbedder(BaseEmbedder):
    """Pathway wrapper for OpenAI Embedding services.

    The capacity, retry_strategy and cache_strategy need to be specified during object
    construction. All other arguments can be overridden during application.

    Args:
        capacity: Maximum number of concurrent operations allowed.
            Defaults to None, indicating no specific limit.
        retry_strategy: Strategy for handling retries in case of failures.
            Defaults to None, meaning no retries.
        cache_strategy: Defines the caching mechanism. To enable caching,
            a valid `CacheStrategy` should be provided.
            See `Cache strategy <https://pathway.com/developers/api-docs/udfs#pathway.udfs.CacheStrategy>`_
            for more information. Defaults to None.
        model: ID of the model to use. You can use the
            `List models <https://platform.openai.com/docs/api-reference/models/list>`_ API to
            see all of your available models, or see
            `Model overview <https://platform.openai.com/docs/models/overview>`_ for
            descriptions of them.
        encoding_format: The format to return the embeddings in. Can be either `float` or
            `base64 <https://pypi.org/project/pybase64/>`_.
        user: A unique identifier representing your end-user, which can help OpenAI to monitor
            and detect abuse.
            `Learn more <https://platform.openai.com/docs/guides/safety-best-practices/end-user-ids>`_.
        extra_headers: Send extra headers
        extra_query: Add additional query parameters to the request
        extra_body: Add additional JSON properties to the request
        timeout: Timeout for requests, in seconds

    Any arguments can be provided either to the constructor or in the UDF call.
    To specify the `model` in the UDF call, set it to None.

    Example:

    >>> import pathway as pw
    >>> from pathway.xpacks.llm import embedders
    >>> embedder = embedders.OpenAIEmbedder(model="text-embedding-ada-002")
    >>> t = pw.debug.table_from_markdown('''
    ... txt
    ... Text
    ... ''')
    >>> t.select(ret=embedder(pw.this.txt))
    <pathway.Table schema={'ret': numpy.ndarray[typing.Any, numpy.dtype[typing.Any]]}>

    >>> import pathway as pw
    >>> from pathway.xpacks.llm import embedders
    >>> embedder = embedders.OpenAIEmbedder()
    >>> t = pw.debug.table_from_markdown('''
    ... txt  | model
    ... Text | text-embedding-ada-002
    ... ''')
    >>> t.select(ret=embedder(pw.this.txt, model=pw.this.model))
    <pathway.Table schema={'ret': numpy.ndarray[typing.Any, numpy.dtype[typing.Any]]}>
    """

    def __init__(
        self,
        *,
        capacity: int | None = None,
        retry_strategy: udfs.AsyncRetryStrategy | None = None,
        cache_strategy: udfs.CacheStrategy | None = None,
        model: str | None = "text-embedding-ada-002",
        **openai_kwargs,
    ):
        with optional_imports("xpack-llm"):
            import openai  # noqa:F401

        _monkeypatch_openai_async()
        executor = udfs.async_executor(capacity=capacity, retry_strategy=retry_strategy)
        super().__init__(
            executor=executor,
            cache_strategy=cache_strategy,
        )
        self.kwargs = dict(openai_kwargs)
        if model is not None:
            self.kwargs["model"] = model

    async def __wrapped__(self, input, **kwargs) -> np.ndarray:
        """Embed the documents

        Args:
            input: mandatory, the string to embed.
            **kwargs: optional parameters, if unset defaults from the constructor
              will be taken.
        """
        import openai

        kwargs = {**self.kwargs, **kwargs}
        api_key = kwargs.pop("api_key", None)
        client = openai.AsyncOpenAI(api_key=api_key)
        ret = await client.embeddings.create(input=[input or "."], **kwargs)
        return np.array(ret.data[0].embedding)


class LiteLLMEmbedder(BaseEmbedder):
    """Pathway wrapper for `litellm.embedding`.

    Model has to be specified either in constructor call or in each application, no default
    is provided. The capacity, retry_strategy and cache_strategy need to be specified
    during object construction. All other arguments can be overridden during application.

    Args:
        capacity: Maximum number of concurrent operations allowed.
            Defaults to None, indicating no specific limit.
        retry_strategy: Strategy for handling retries in case of failures.
            Defaults to None, meaning no retries.
        cache_strategy: Defines the caching mechanism. To enable caching,
            a valid `CacheStrategy` should be provided.
            See `Cache strategy <https://pathway.com/developers/api-docs/udfs#pathway.udfs.CacheStrategy>`_
            for more information. Defaults to None.
        model: The embedding model to use.
        timeout: The timeout value for the API call, default 10 mins
        litellm_call_id: The call ID for litellm logging.
        litellm_logging_obj: The litellm logging object.
        logger_fn: The logger function.
        api_base: Optional. The base URL for the API.
        api_version: Optional. The version of the API.
        api_key: Optional. The API key to use.
        api_type: Optional. The type of the API.
        custom_llm_provider: The custom llm provider.

    Any arguments can be provided either to the constructor or in the UDF call.
    To specify the `model` in the UDF call, set it to None.

    Example:

    >>> import pathway as pw
    >>> from pathway.xpacks.llm import embedders
    >>> embedder = embedders.LiteLLMEmbedder(model="text-embedding-ada-002")
    >>> t = pw.debug.table_from_markdown('''
    ... txt
    ... Text
    ... ''')
    >>> t.select(ret=embedder(pw.this.txt))
    <pathway.Table schema={'ret': numpy.ndarray[typing.Any, numpy.dtype[typing.Any]]}>

    >>> import pathway as pw
    >>> from pathway.xpacks.llm import embedders
    >>> embedder = embedders.LiteLLMEmbedder()
    >>> t = pw.debug.table_from_markdown('''
    ... txt  | model
    ... Text | text-embedding-ada-002
    ... ''')
    >>> t.select(ret=embedder(pw.this.txt, model=pw.this.model))
    <pathway.Table schema={'ret': numpy.ndarray[typing.Any, numpy.dtype[typing.Any]]}>
    """

    def __init__(
        self,
        *,
        capacity: int | None = None,
        retry_strategy: udfs.AsyncRetryStrategy | None = None,
        cache_strategy: udfs.CacheStrategy | None = None,
        model: str | None = None,
        **llmlite_kwargs,
    ):
        with optional_imports("xpack-llm"):
            import litellm  # noqa:F401

        _monkeypatch_openai_async()
        executor = udfs.async_executor(capacity=capacity, retry_strategy=retry_strategy)
        super().__init__(
            executor=executor,
            cache_strategy=cache_strategy,
        )
        self.kwargs = dict(llmlite_kwargs)
        if model is not None:
            self.kwargs["model"] = model

    async def __wrapped__(self, input, **kwargs) -> np.ndarray:
        """Embed the documents

        Args:
            input: mandatory, the string to embed.
            **kwargs: optional parameters, if unset defaults from the constructor
              will be taken.
        """
        import litellm

        kwargs = {**self.kwargs, **kwargs}
        ret = await litellm.aembedding(input=[input or "."], **kwargs)
        return np.array(ret.data[0]["embedding"])


class SentenceTransformerEmbedder(BaseEmbedder):
    """
    Pathway wrapper for Sentence-Transformers embedder.

    Args:
        model: model name or path
        call_kwargs: kwargs that will be passed to each call of encode.
            These can be overridden during each application. For possible arguments check
            `the Sentence-Transformers documentation
            <https://www.sbert.net/docs/package_reference/SentenceTransformer.html#sentence_transformers.SentenceTransformer.encode>`_.
        device: defines which device will be used to run the Pipeline
        sentencetransformer_kwargs: kwargs accepted during initialization of SentenceTransformers.
            For possible arguments check
            `the Sentence-Transformers documentation
            <https://www.sbert.net/docs/package_reference/SentenceTransformer.html#sentence_transformers.SentenceTransformer>`_

    Example:

    >>> import pathway as pw
    >>> from pathway.xpacks.llm import embedders
    >>> embedder = embedders.SentenceTransformerEmbedder(model="intfloat/e5-large-v2")
    >>> t = pw.debug.table_from_markdown('''
    ... txt
    ... Text
    ... ''')
    >>> t.select(ret=embedder(pw.this.txt))
    <pathway.Table schema={'ret': numpy.ndarray[typing.Any, numpy.dtype[typing.Any]]}>
    """  # noqa: E501

    def __init__(
        self,
        model: str,
        call_kwargs: dict = {},
        device: str = "cpu",
        **sentencetransformer_kwargs,
    ):
        with optional_imports("xpack-llm-local"):
            from sentence_transformers import SentenceTransformer

        super().__init__()
        self.model = SentenceTransformer(
            model_name_or_path=model, device=device, **sentencetransformer_kwargs
        )
        self.kwargs = call_kwargs

    def __wrapped__(self, input: str, **kwargs) -> np.ndarray:
        """
        Embed the text

        Args:
            input: mandatory, the string to embed.
            **kwargs: optional parameters for `encode` method. If unset defaults from the constructor
              will be taken. For possible arguments check
              `the Sentence-Transformers documentation
              <https://www.sbert.net/docs/package_reference/SentenceTransformer.html#sentence_transformers.SentenceTransformer.encode>`_.
        """  # noqa: E501
        kwargs = {**self.kwargs, **kwargs}
        return self.model.encode(input, **kwargs)


class GeminiEmbedder(BaseEmbedder):
    """Pathway wrapper for Google Gemini Embedding services.

    The ``capacity``, ``retry_strategy`` and ``cache_strategy`` need to be specified during object
    construction. All other arguments can be overridden during application.

    Args:
        capacity: Maximum number of concurrent operations allowed.
            Defaults to ``None``, indicating no specific limit.
        retry_strategy: Strategy for handling retries in case of failures.
            Defaults to ``None``, meaning no retries.
        cache_strategy: Defines the caching mechanism. To enable caching,
            a valid ``CacheStrategy`` should be provided.
            See `Cache strategy <https://pathway.com/developers/api-docs/udfs#pathway.udfs.CacheStrategy>`_
            for more information. Defaults to None.
        model: ID of the model to use. Check the
            `Gemini documentation <https://ai.google.dev/gemini-api/docs/models/gemini#text-embedding-and-embedding>`_
            for list of available models. To specify the `model` in the UDF call, set it to None in the constructor.
        api_key: API key for Gemini API services. Can be provided in the constructor,
            in ``__call__`` or by setting ``GOOGLE_API_KEY`` environment variable
        gemini_kwargs: any other arguments accepted by gemini embedding service. Check
            the `Gemini documentation <https://ai.google.dev/api/embeddings#method:-models.embedcontent>`_
            for list of accepted arguments.

    Example:

    >>> import pathway as pw
    >>> from pathway.xpacks.llm import embedders
    >>> embedder = embedders.GeminiEmbedder(model="models/text-embedding-004")
    >>> t = pw.debug.table_from_markdown('''
    ... txt
    ... Text
    ... ''')
    >>> t.select(ret=embedder(pw.this.txt))
    <pathway.Table schema={'ret': numpy.ndarray[typing.Any, numpy.dtype[typing.Any]]}>

    >>> import pathway as pw
    >>> from pathway.xpacks.llm import embedders
    >>> embedder = embedders.GeminiEmbedder()
    >>> t = pw.debug.table_from_markdown('''
    ... txt  | model
    ... Text | models/embedding-001
    ... ''')
    >>> t.select(ret=embedder(pw.this.txt, model=pw.this.model))
    <pathway.Table schema={'ret': numpy.ndarray[typing.Any, numpy.dtype[typing.Any]]}>
    """

    def __init__(
        self,
        *,
        capacity: int | None = None,
        retry_strategy: udfs.AsyncRetryStrategy | None = None,
        cache_strategy: udfs.CacheStrategy | None = None,
        model: str | None = "models/embedding-001",
        api_key: str | None = None,
        **gemini_kwargs,
    ):
        with optional_imports("xpack-llm"):
            import google.generativeai as genai  # noqa: 401

        executor = udfs.async_executor(capacity=capacity, retry_strategy=retry_strategy)
        super().__init__(
            executor=executor,
            cache_strategy=cache_strategy,
        )
        self.kwargs = dict(gemini_kwargs)
        if model is not None:
            self.kwargs["model"] = model
        if api_key is not None:
            self.kwargs["api_key"] = api_key

    def __wrapped__(self, input: str, **kwargs) -> np.ndarray:
        import google.generativeai as genai

        kwargs = {**self.kwargs, **kwargs}
        model = kwargs.pop("model", None)

        api_key = kwargs.pop("api_key", None)
        if api_key is not None:
            genai.configure(api_key=api_key)

        response = genai.embed_content(model, content=[input], **kwargs)
        embedding = response["embedding"][0]
        return np.array(embedding)