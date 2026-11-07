import { useEffect, useState, type ReactNode } from 'react'
import type { Board, SetupChoices, Sibling } from './api'
import { WORDS } from './words'
import './child.css'

// 家族の設定。問いは選ぶ形で、自由な文字は子のあだ名の1欄だけ。あだ名はサーバーで呼び名として持ち、
// AIへ送る前に［子］へ伏せる（choices には入れない）。入院した日はAIに渡さない。
// 連絡する相手は、初めから3つとも選んだ形（触らずに進んでも、下書きが消えないように）。
// 2026-11-08 PO（夫の初見）：入院している子の年齢・性別・通園・通学先、家にいるきょうだいの人数と
// 1人ずつの答え、動ける時間帯（いくつでも）を足し、支援の問いを外した。どれも選ぶ形だけ（名前の欄は無い）。
type Option = string | { value: string; label: string }
const valueOf = (option: Option) => (typeof option === 'string' ? option : option.value)
const labelOf = (option: Option) => (typeof option === 'string' ? option : option.label)

const ALL_CONTACTS = WORDS.setup.contactOptions.map(valueOf)
// サーバーの NICKNAME_MAX_CHARS と同じ。
const NICKNAME_MAX = 8
// あだ名は台帳の文の中を部分一致で伏せるので、1文字・条件・日付・医療・症状の語と重なる語は使えない。
// サーバー（guards.py の NICKNAME_BLOCKED）と同じ表（test_child_facts が突き合わせる）。最後はサーバーが決める。
const NICKNAME_BLOCKED = [
  'なら', 'かも', 'かな', '予定', '見込み', 'しだい', '次第', '退院', '入院', '検査', '採血',
  '確定', '候補', '延長', '迎え', '先生', '病院', '病棟', '保育園', '職場', '家族', '明日', '今日',
  '夫', '母', '父', '妻', '男の子', '女の子', '幼稚園', '学校', 'きょうだい', '通う', '祖父母',
  '薬', '量', 'mg', 'ミリグラム', '投与', '中止', '増やす', '減らす', '服用',
  '点滴', '副作用', '解熱', 'crp', '明後日', 'あさって', 'あした', '昨日', '今夜', '今晩', '今朝',
  '今週', '来週', '再来週', '週末', 'けいれん', '痙攣', 'ひきつけ', 'ぐったり', '呼吸', '意識',
  '顔色', '嘔吐', '出血', '血便', '下痢', '発疹', '湿疹', 'じんましん', '腫れ', 'むくみ', '痛い',
  '痛み', '震え', 'ふるえ', '高熱', '咳', 'せき', 'おしっこ', 'ゼーゼー', 'チアノーゼ', 'ゼイゼイ',
  'ヒューヒュー', '真っ青', '青白', '唇が紫', '血を吐', '目を覚まさない', '呼びかけ',
]
export function nicknameAllowed(raw: string): boolean {
  const name = raw.trim()
  if (!name) return true
  if (name.length < 2 || /[0-9０-９日月時曜]/.test(name)) return false
  const folded = name.normalize('NFKC').toLowerCase()
  return !NICKNAME_BLOCKED.some((word) => word.includes(folded) || folded.includes(word))
}
// サーバーの SIBLINGS_MAX と同じ。
const SIBLINGS_MAX = 20
const AGES = Array.from({ length: 19 }, (_, n) => n)
const TIME_ORDER: readonly string[] = WORDS.setup.errandTimeOptions
// 外した2問（一緒に暮らす大人・家でお世話が必要な子）は持たない。古い台帳の答えは startFrom で残る。
// 支援の問いは外したので、新しい設定は空で送る（古い台帳の答えは startFrom でそのまま送り返す）。
const EMPTY: SetupChoices = {
  parent: null,
  work: null,
  errand: null,
  bedside: null,
  supports: [],
  no_support: false,
  contacts: ALL_CONTACTS,
  child_age: null,
  child_sex: null,
  child_school: null,
  siblings: [],
  errand_times: [],
  nickname: '',
  admitted_on: '',
}

// 前の画面の答え（きょうだいの通園・通学先、動ける時間の1択）を、今の問いへ移す。見えない答えを残さない。
const OLD_SCHOOL: Record<string, string> = { 保育園: '保育園・幼稚園', 学校: '学校' }
const OLD_TIMES: Record<string, string[]> = {
  平日の夜と朝: ['平日の朝', '平日の夜'],
  休みの日だけ: ['休みの日'],
  日中も動ける: ['平日の日中', '平日の夜'],
}

// 前に保存した内容に連絡する相手が無ければ（この欄を足す前の家庭）、3つとも選んだ形で開く。
function startFrom(initial: SetupChoices | null): SetupChoices {
  if (!initial) return EMPTY
  const { school, errand_time, ...rest } = { ...EMPTY, ...initial }
  const oldSchool = school ? OLD_SCHOOL[school] : undefined
  return {
    ...rest,
    contacts: initial.contacts ?? ALL_CONTACTS,
    siblings:
      initial.siblings ?? (oldSchool ? [{ age: null, sex: null, school: oldSchool }] : []),
    errand_times: initial.errand_times ?? (errand_time ? OLD_TIMES[errand_time] ?? [] : []),
  }
}

// 直すときの初めの内容。あだ名と入院した日は choices の外にあるので、ここで混ぜる。
export function setupInitial(board: Board): SetupChoices | null {
  const child = { nickname: board.nickname ?? '', admitted_on: board.admitted_on ?? '' }
  if (board.profile.choices) return { ...board.profile.choices, ...child }
  return board.nickname || board.admitted_on ? { ...EMPTY, ...child } : null
}

type Single = 'parent' | 'work' | 'errand' | 'bedside'
type ChildAnswer = { age: number | null; sex: string | null; school: string | null }

// 子1人の答え（年齢・性別・通園・通学先）。入院している子と、きょうだいの1行に同じ形を使う。
// 選ぶ形だけ。data-setup は「prefix.age」などで、場面の検査が押す先にする。
function ChildFacts({
  prefix,
  who,
  value,
  onChange,
}: {
  prefix: string
  who: string
  value: ChildAnswer
  onChange: (next: ChildAnswer) => void
}) {
  const words = WORDS.setup
  return (
    <div className="child-facts">
      <div className="child-selects">
        <label className="child-select">
          <span className="detail">{words.age}</span>
          <select
            className="setup-select"
            aria-label={`${who}の${words.age}`}
            data-setup={`${prefix}.age`}
            value={value.age === null ? '' : String(value.age)}
            onChange={(event) =>
              onChange({
                ...value,
                age: event.target.value === '' ? null : Number(event.target.value),
              })
            }
          >
            <option value="">{words.ageNone}</option>
            {AGES.map((n) => (
              <option key={n} value={String(n)}>
                {words.ageOption(n)}
              </option>
            ))}
          </select>
        </label>
        <label className="child-select">
          <span className="detail">{words.childSchool}</span>
          <select
            className="setup-select"
            aria-label={`${who}の${words.childSchool}`}
            data-setup={`${prefix}.school`}
            value={value.school ?? ''}
            onChange={(event) => onChange({ ...value, school: event.target.value || null })}
          >
            <option value="">{words.ageNone}</option>
            {words.childSchoolOptions.map((option) => (
              <option key={option} value={option}>
                {option === 'なし' ? words.schoolNoneLabel : option}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="child-sex" role="group" aria-label={`${who}の${words.sex}`}>
        <span className="detail">{words.sex}</span>
        <div className="setup-options even">
          {words.sexOptions.map((option) => (
            <button
              type="button"
              key={option}
              className="choice"
              aria-pressed={value.sex === option}
              data-setup={`${prefix}.sex:${option}`}
              onClick={() => onChange({ ...value, sex: value.sex === option ? null : option })}
            >
              {option}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function CheckMark() {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 20 20">
      <path
        d="M4 10.5l4 4 8-9"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function SetupScreen({
  initial,
  editing = false,
  busy,
  message,
  onSave,
  onLater,
  footer,
}: {
  initial: SetupChoices | null
  editing?: boolean
  busy: boolean
  message: string | null
  onSave: (choices: SetupChoices) => void
  // 最初の画面では、そこまで選んだ内容（連絡する相手を含む）も渡す。
  onLater: (choices: SetupChoices) => void
  // 1ページ目の一番下に置くもの（最初の画面の「今ある記録から始める」）。
  footer?: ReactNode
}) {
  const words = WORDS.setup
  const [page, setPage] = useState<1 | 2>(1)
  const [choices, setChoices] = useState<SetupChoices>(() => startFrom(initial))
  const nicknameBad = !nicknameAllowed(choices.nickname ?? '')

  // ページを移ったら頭から見せる。ボタンは一番下にあるので、そのままだと次のページの下から始まる。
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [page])

  const pick = (key: Single, value: string) =>
    setChoices((current) => {
      const chosen = current[key] === value ? null : value
      const next = { ...current, [key]: chosen }
      // 家のことが中心なら、職場への文は作らない。チェックを外すだけなので、押せば戻せる。
      if (key === 'work' && chosen === words.homeWork) {
        next.contacts = (current.contacts ?? ALL_CONTACTS).filter(
          (name) => name !== words.workplace,
        )
      }
      // 家のことが中心から別の答えに変えたら、外した職場を戻す（QA Test の P2）。
      if (key === 'work' && current.work === words.homeWork && chosen !== words.homeWork) {
        const contacts = current.contacts ?? ALL_CONTACTS
        if (!contacts.includes(words.workplace)) next.contacts = [...contacts, words.workplace]
      }
      return next
    })

  // 問いの下の1行は「この答えで変わること」。札と台帳の値は分ける（data-setup は値）。
  const single = (
    key: Single,
    title: string,
    detail: string,
    options: readonly Option[],
    even = false,
  ) => (
    <fieldset className="setup-field" key={key}>
      <legend>{title}</legend>
      <p className="detail setup-detail">{detail}</p>
      <div className={even ? 'setup-options even' : 'setup-options'}>
        {options.map((option) => {
          const value = valueOf(option)
          return (
            <button
              type="button"
              key={value}
              className="choice"
              aria-pressed={choices[key] === value}
              data-setup={`${key}:${value}`}
              onClick={() => pick(key, value)}
            >
              {labelOf(option)}
            </button>
          )
        })}
      </div>
    </fieldset>
  )

  const siblings = choices.siblings ?? []
  // 人数を減らすと、最後の行から消す。増やすと、空の行を足す。
  const setSiblingCount = (count: number) =>
    setChoices((current) => {
      const now = current.siblings ?? []
      const size = Math.max(0, Math.min(SIBLINGS_MAX, count))
      const blank: Sibling = { age: null, sex: null, school: null }
      return {
        ...current,
        siblings:
          size <= now.length
            ? now.slice(0, size)
            : [...now, ...Array.from({ length: size - now.length }, () => ({ ...blank }))],
      }
    })
  const setSibling = (index: number, next: Sibling) =>
    setChoices((current) => ({
      ...current,
      siblings: (current.siblings ?? []).map((entry, place) => (place === index ? next : entry)),
    }))

  const times = choices.errand_times ?? []
  const toggleTime = (name: string) =>
    setChoices((current) => {
      const now = current.errand_times ?? []
      return {
        ...current,
        errand_times: now.includes(name)
          ? now.filter((entry) => entry !== name)
          : TIME_ORDER.filter((entry) => entry === name || now.includes(entry)),
      }
    })

  const contacts = choices.contacts ?? ALL_CONTACTS
  const toggleContact = (name: string) =>
    setChoices((current) => {
      const now = current.contacts ?? ALL_CONTACTS
      return {
        ...current,
        contacts: now.includes(name)
          ? now.filter((entry) => entry !== name)
          : ALL_CONTACTS.filter((entry) => entry === name || now.includes(entry)),
      }
    })

  return (
    <div className="with-dock setup-page">
      <main className="setup-screen" data-setup-screen data-setup-step={page}>
        <p className="detail">{editing ? words.stepEdit(page) : words.step(page)}</p>
        <div
          className="setup-progress"
          role="progressbar"
          aria-label={words.progress}
          aria-valuemin={0}
          aria-valuemax={2}
          aria-valuenow={page}
          data-setup-progress
        >
          <span style={{ width: page === 1 ? '50%' : '100%' }} />
        </div>
        <h1>{page === 1 ? words.title1 : words.title2}</h1>
        <div className="setup-lead">
          <p className="detail">{words.lead}</p>
          <p className="detail">{words.noRealNames}</p>
        </div>
        {message && (
          <p className="message" role="alert">
            {message}
          </p>
        )}
        {page === 1 ? (
          <>
            {/* どちらも空でよい。あだ名が空なら、画面では「お子さん」。 */}
            <fieldset className="setup-field" data-nickname>
              <legend>{words.nickname}</legend>
              <p className="detail setup-detail">{words.nicknameDetail}</p>
              <input
                type="text"
                className="setup-input"
                maxLength={NICKNAME_MAX}
                autoComplete="off"
                aria-label={words.nickname}
                value={choices.nickname ?? ''}
                onChange={(event) =>
                  setChoices((current) => ({ ...current, nickname: event.target.value }))
                }
              />
              {nicknameBad && (
                <p className="detail" role="alert" data-nickname-refused>
                  {words.nicknameRefused}
                </p>
              )}
            </fieldset>
            <fieldset className="setup-field" data-admitted>
              <legend>{words.admittedOn}</legend>
              <p className="detail setup-detail">{words.admittedOnDetail}</p>
              <input
                type="date"
                className="setup-input"
                aria-label={words.admittedOn}
                value={choices.admitted_on ?? ''}
                onChange={(event) =>
                  setChoices((current) => ({ ...current, admitted_on: event.target.value }))
                }
              />
            </fieldset>
            <fieldset className="setup-field" data-child>
              <legend>{words.child}</legend>
              <p className="detail setup-detail">{words.childDetail}</p>
              <ChildFacts
                prefix="child"
                who={words.child}
                value={{
                  age: choices.child_age ?? null,
                  sex: choices.child_sex ?? null,
                  school: choices.child_school ?? null,
                }}
                onChange={(next) =>
                  setChoices((current) => ({
                    ...current,
                    child_age: next.age,
                    child_sex: next.sex,
                    child_school: next.school,
                  }))
                }
              />
            </fieldset>
            {single('parent', words.parent, words.parentDetail, words.parentOptions, true)}
            <fieldset className="setup-field" data-siblings>
              <legend>{words.siblings}</legend>
              <p className="detail setup-detail">{words.siblingsDetail}</p>
              <div className="sibling-stepper">
                <button
                  type="button"
                  aria-label={words.siblingFewer}
                  data-sibling-fewer
                  disabled={siblings.length === 0}
                  onClick={() => setSiblingCount(siblings.length - 1)}
                >
                  −
                </button>
                <span className="sibling-count" aria-live="polite" data-sibling-count>
                  {words.siblingCount(siblings.length)}
                </span>
                <button
                  type="button"
                  aria-label={words.siblingMore}
                  data-sibling-more
                  disabled={siblings.length >= SIBLINGS_MAX}
                  onClick={() => setSiblingCount(siblings.length + 1)}
                >
                  ＋
                </button>
              </div>
              {siblings.length > 0 && (
                <div className="sibling-list">
                  {siblings.map((sibling, index) => (
                    <div className="sibling-row" key={index} data-sibling={index}>
                      <p className="sibling-head">{words.siblingNth(index + 1)}</p>
                      <ChildFacts
                        prefix={`siblings.${index}`}
                        who={words.siblingNth(index + 1)}
                        value={sibling}
                        onChange={(next) => setSibling(index, next)}
                      />
                    </div>
                  ))}
                </div>
              )}
            </fieldset>
            {single('work', words.work, words.workDetail, words.workOptions)}
            <fieldset className="setup-field" data-contacts>
              <legend>{words.contacts}</legend>
              <p className="detail setup-detail">{words.contactsDetail}</p>
              <div className="setup-options">
                {words.contactOptions.map(({ value, label }) => (
                  <button
                    type="button"
                    key={value}
                    role="checkbox"
                    className="choice contact"
                    aria-checked={contacts.includes(value)}
                    data-contact={value}
                    data-setup={`contacts:${value}`}
                    onClick={() => toggleContact(value)}
                  >
                    <span className="contact-box">
                      {contacts.includes(value) && <CheckMark />}
                    </span>
                    {label}
                  </button>
                ))}
              </div>
            </fieldset>
          </>
        ) : (
          <>
            {single('errand', words.errand, words.errandDetail, words.errandOptions)}
            {choices.errand && choices.errand !== words.nobody ? (
              <fieldset className="setup-field" data-errand-times>
                <legend>{words.errandTimes}</legend>
                <p className="detail setup-detail">{words.errandTimesDetail}</p>
                <div className="setup-options">
                  {words.errandTimeOptions.map((option) => (
                    <button
                      type="button"
                      key={option}
                      role="checkbox"
                      className="choice contact time"
                      aria-checked={times.includes(option)}
                      data-setup={`errand_times:${option}`}
                      onClick={() => toggleTime(option)}
                    >
                      <span className="contact-box">
                        {times.includes(option) && <CheckMark />}
                      </span>
                      {option}
                    </button>
                  ))}
                </div>
              </fieldset>
            ) : null}
            {single('bedside', words.bedside, words.bedsideDetail, words.yesNo)}
          </>
        )}
      </main>
      {/* 図のとおり、ボタンは選ぶ欄の続き。欄が画面より長い間は、画面の下に張り付けて見せる。 */}
      <div className="dock setup-actions" data-setup-actions>
        {page === 1 ? (
          <>
            {/* 押せない理由をボタンの近くにも出す。欄の下の理由は、画面の下からは見えない（2026-11-08 本人の実機）。
                読み上げは欄の下の1つ（role=alert）に任せ、ここは重ねない。 */}
            {nicknameBad && (
              <p className="detail" data-nickname-refused-near>
                {words.nicknameRefused}
              </p>
            )}
            <button
              type="button"
              className="primary"
              data-primary
              disabled={nicknameBad}
              onClick={() => setPage(2)}
            >
              {words.next}
            </button>
            <p className="detail setup-next">{words.nextLead(words.title2)}</p>
            {/* 直すときは2ページ目の［戻る］が1ページ目へ戻るので、1ページ目でだけ閉じる。
                直すときの［戻る］は保存せずに閉じるだけなので、断るあだ名でも押せる（2026-11-08 夫の初見：
                「み」で次へも戻るも押せず、閉じ込められた）。最初の［あとで設定する］は選んだ内容で始めるので止める。 */}
            <button
              type="button"
              disabled={busy || (!editing && nicknameBad)}
              onClick={() => onLater(choices)}
            >
              {editing ? WORDS.add.back : words.later}
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="primary"
              data-primary
              disabled={busy}
              onClick={() => onSave(choices)}
            >
              {editing ? words.saveEdit : words.save}
            </button>
            <div className="dock-pair">
              <button type="button" onClick={() => setPage(1)}>
                {WORDS.add.back}
              </button>
              {!editing && (
                <button type="button" disabled={busy} onClick={() => onLater(choices)}>
                  {words.later}
                </button>
              )}
            </div>
          </>
        )}
      </div>
      {page === 1 && footer}
    </div>
  )
}
