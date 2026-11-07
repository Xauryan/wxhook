from dataclasses import asdict
from datetime import datetime
import json
import re
import time

from ..ai.models import ChatMessage
from ..ai.service import AIError
from ..ai.decisions import DecisionUnavailable
from ..retrieval.models import RetrievalError
from ..retrieval.service import evidence_chunks
from .models import AnswerDraft, AnswerReview, QueryPlan

PIPELINE_VERSION = 'group-qa-4'
from .prompts import DECISION_CHOICES, DECISION_PROMPT, PLAN_PROMPT, ANSWER_PROMPT, REVIEW_PROMPT, PROMPTS

# Quick stages only classify or check; low reasoning keeps them within a chat-friendly delay.
FAST_STAGES = {'plan', 'verify'}
# An @mention is '@name' then U+2005, or whitespace / end of text when typed by hand.
MENTION = re.compile(r'@[^\s\u2005@]{1,40}(?:\u2005|\s|$)')
NOISE = re.compile(r'\[[^\[\]]{1,8}\]|[\W_]+', re.UNICODE)


def is_forced(text):
    return text.startswith('/ask')


def is_explicit(current, config):
    """/ask, an @mention of the assistant, or a quote-reply to the assistant's own message."""
    if is_forced(current['text'].strip()):
        return True
    if not config.mention_trigger:
        return False
    return bool(current.get('mentions_assistant') or (current.get('quote') or {}).get('from_assistant'))


def question_text(text):
    """The user's words with the /ask prefix and @mentions removed."""
    text = text.strip()
    if text.startswith('/ask'):
        text = text[4:]
    return MENTION.sub(' ', text).strip()


def candidate_reason(current, config):
    text = current['text'].strip()
    if is_explicit(current, config):
        return ''
    if current.get('is_self'):
        return 'self_message'
    if not config.proactive:
        return 'proactive_disabled'
    if current.get('category') not in {'text', 'quote'}:
        return 'not_text'
    # WeChat emoji codes such as [发呆] and punctuation-only replies ("？", "这？") carry no question.
    meaningful = NOISE.sub('', text)
    if len(meaningful) < max(2, config.local_min_text_chars):
        return 'empty_content'
    if config.local_require_question_signal and not re.search(config.local_question_pattern, text):
        return 'not_question'
    return ''


def parse_json(text, schema):
    value = text.strip()
    if value.startswith('```'):
        value = re.sub(r'^```(?:json)?\s*', '', value)
        value = re.sub(r'\s*```$', '', value)
    try:
        return schema.model_validate_json(value)
    except ValueError:
        raise AIError('模型返回格式无效') from None


def compact_text(value):
    return re.sub(r'\s+', '', value)


def source_footer(sources, style):
    """Short source line for WeChat; long URLs crowd a chat bubble."""
    if not sources:
        return ''
    if style == 'names':
        names = []
        for source in sources[:3]:
            site = 'BWIKI' if source.get('source_kind') == 'community_wiki' else (
                '官方' if source.get('source_kind') == 'official' else '网页')
            names.append(f"{site}《{source['title'][:30]}》")
        return '来源：' + '、'.join(dict.fromkeys(names))
    urls = list(dict.fromkeys(source['url'] for source in sources))
    if style == 'one_link':
        return '来源：' + urls[0]
    return '\n'.join(f'[{index + 1}] {url}' for index, url in enumerate(urls[:3]))


class ReplyEngine:
    def __init__(self, ai, retriever, decisions=None):
        self.ai, self.retriever, self.decisions = ai, retriever, decisions

    async def evaluate(self, context, config, *, model_revision, last_reply_at=0, profile_revisions=None,
                       faq_lookup=None):
        started = time.monotonic()
        # /ask always gets a reply. An @mention or follow-up on this personal account skips the chat
        # filter and cooldown, but stays silent without a verified answer: people @ the owner for many reasons.
        forced = is_forced(context['current']['text'].strip())
        explicit = is_explicit(context['current'], config)
        trigger = 'ask' if forced else ('mention' if explicit else 'proactive')
        result = {'pipeline': PIPELINE_VERSION, 'trigger': trigger,
                  'search_mode': config.search_mode, 'should_reply': False, 'answer': '',
                  'reason': '', 'confidence': 0, 'plan': None, 'draft': None, 'review': None,
                  'decision': None, 'evidence': [], 'searches': [], 'retrieval_errors': [], 'usage': {},
                  'attempts': [], 'sources': [], 'body': ''}

        def finish(reason, detail='', answer=''):
            result.update(reason=reason, detail=detail, answer=answer, should_reply=bool(answer),
                          elapsed_ms=round((time.monotonic() - started) * 1000))
            if forced and not answer:
                message = detail if reason in {'missing_context', 'group_local'} else '暂时没查到足够明确的资料来确认这个问题。'
                result.update(should_reply=True, answer=message or '请补充你想问的具体问题。')
            return result

        def stage_profiles(purpose):
            primary = {'plan': config.planning_profile_id, 'verify': config.review_profile_id}.get(purpose) or config.ai_profile_id
            return list(dict.fromkeys([primary, *config.fallback_profile_ids]))

        async def call(prompt, data, schema, purpose):
            last_error = None
            for profile_id in stage_profiles(purpose):
                revision = model_revision if profile_id == config.ai_profile_id else (profile_revisions or {}).get(profile_id)
                attempt = {'stage': purpose, 'profile_id': profile_id}
                try:
                    if profile_revisions and profile_id in profile_revisions and revision is None:
                        raise AIError('任务创建时该阶段的模型配置不可用')
                    response = await self.ai.complete(profile_id,
                        [ChatMessage(role='system', content=prompt),
                         ChatMessage(role='user', content=json.dumps(data, ensure_ascii=False))],
                        purpose='group_reply.' + purpose, expected_revision=revision,
                        reasoning_effort='low' if config.fast_stages and purpose in FAST_STAGES else None)
                    for key, value in response.usage.items():
                        result['usage'][key] = result['usage'].get(key, 0) + value
                    parsed = parse_json(response.text, schema)
                    result['attempts'].append({**attempt, 'status': 'completed'})
                    return parsed
                except AIError as error:
                    last_error = error
                    result['attempts'].append({**attempt, 'status': 'failed', 'detail': str(error)[:120]})
            raise last_error

        def with_glossary(data):
            return {**data, 'group_glossary': config.glossary} if config.glossary.strip() else data

        try:
            reason = candidate_reason(context['current'], config)
            if reason:
                return finish(reason)
            if explicit and not question_text(context['current']['text']) and not context['current'].get('quote'):
                return finish('missing_context', '你想问什么？请在 /ask 后面写上具体问题。')
            at = datetime.fromisoformat(context['at'])
            if not explicit and last_reply_at and 0 <= at.timestamp() - last_reply_at < config.cooldown_seconds:
                return finish('cooldown', '处于群内主动回复间隔内')
            if not explicit and self.decisions is not None and not config.decision_profile_ids:
                return finish('decision_unavailable', '请配置至少一个决策模型')
            if not explicit and config.decision_profile_ids:
                if self.decisions is None:
                    return finish('decision_unavailable', '决策服务未初始化')
                try:
                    decision = await self.decisions.choose_chain(config.decision_profile_ids, context,
                        (config.decision_prompt or DECISION_PROMPT), DECISION_CHOICES, revisions=profile_revisions)
                except DecisionUnavailable as error:
                    result['decision'] = {'attempts': error.attempts}
                    return finish('decision_unavailable', str(error))
                result['decision'] = decision
                if decision['choice'] == 'ignore' and decision['probabilities']['ignore'] >= config.decision_skip_threshold:
                    return finish('not_actionable', '决策模型判定为明确的非求助消息')
            plan = await call(config.plan_prompt or PLAN_PROMPT, with_glossary(context), QueryPlan, 'plan')
            result['plan'] = plan.model_dump()
            if not explicit and result['decision'] is None and not plan.need_answer:
                return finish('not_actionable', plan.reason)
            if not plan.enough_context or not plan.question:
                detail = plan.missing_context.strip() or '请补充具体的游戏、角色或事件，以及你想了解的条件。'
                return finish('missing_context', detail)
            if plan.domain == 'group_local' or plan.intent in {'coordination', 'chitchat', 'rhetorical'}:
                return finish('group_local', '这需要群内成员或活动负责人的确认，可以补充具体公告或相关信息再问我。')
            if faq_lookup is not None:
                hit = faq_lookup(plan.question, plan.domain, plan.time_sensitive)
                if hit:
                    result['faq'] = {'id': hit['id'], 'question': hit['question']}
                    result['sources'] = hit['sources']
                    result['body'] = hit['answer']
                    footer = source_footer(hit['sources'], config.source_style)
                    return finish('faq', '与之前核验过的问题相同，直接复用答案',
                                  hit['answer'] + ('\n\n' + footer if footer else ''))
            search_profile = config.search_profile_id or config.ai_profile_id
            search_revision = model_revision
            if search_profile != config.ai_profile_id:
                _, _, current_revision = self.ai.store.get(search_profile)
                search_revision = (profile_revisions or {}).get(search_profile, current_revision)
                if search_revision is None:
                    raise AIError('任务创建时搜索模型配置不可用')
            retrieval = await self.retriever.retrieve(plan, config.search_mode, search_profile,
                                                       search_revision, at, options=config.retrieval,
                                                       search_prompt=config.search_prompt or PROMPTS['search_prompt'])
            result['searches'] = retrieval.searches
            result['retrieval_errors'] = retrieval.errors
            evidence = evidence_chunks(retrieval, plan, options=config.retrieval)
            result['evidence'] = evidence
            if not evidence:
                return finish('retrieval_empty', '没有取得能支持回答的正文资料')
            payload = with_glossary({'conversation': context, 'question': plan.model_dump(), 'sources': evidence})
            draft = await call(config.answer_prompt or ANSWER_PROMPT, payload, AnswerDraft, 'answer')
            result['draft'] = draft.model_dump()
            result['confidence'] = draft.confidence
            if not draft.confident or draft.confidence < config.confidence_threshold or not draft.answer.strip():
                return finish('insufficient_evidence', draft.missing)
            if len(draft.answer) > config.max_reply_chars or re.search(r'https?://', draft.answer):
                return finish('invalid_answer', '回答长度或引用格式不符合配置')
            by_id = {item['id']: item for item in evidence}
            verified = []
            for citation in draft.citations:
                source = by_id.get(citation.source_id)
                if not source or compact_text(citation.quote) not in compact_text(source['text']):
                    return finish('invalid_citation', '模型引用未能与实际来源逐字匹配')
                verified.append(source)
            if not verified:
                return finish('insufficient_evidence', '回答缺少可验证的来源')
            review = await call(config.review_prompt or REVIEW_PROMPT, {**payload, 'draft': draft.model_dump()},
                                AnswerReview, 'verify')
            result['review'] = review.model_dump()
            result['confidence'] = min(draft.confidence, review.confidence)
            if not (review.supported and review.answers_question and review.version_consistent
                    and review.confidence >= config.confidence_threshold):
                return finish('insufficient_evidence', review.reason)
            sources = list({source['url']: {key: source[key] for key in ('title', 'url', 'source_kind')}
                            for source in verified}.values())
            result['sources'] = sources
            result['body'] = draft.answer.strip()
            footer = source_footer(sources, config.source_style)
            return finish('supported', review.reason, result['body'] + ('\n\n' + footer if footer else ''))
        except (AIError, RetrievalError) as error:
            return finish('error', str(error))
