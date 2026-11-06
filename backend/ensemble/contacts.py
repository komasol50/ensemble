"""連絡する相手（① はじめに のチェック）。

チェックを外した相手には下書きを作らない。AIへの指示に頼らず、案を返す前と保存する前に
コードで落とす（Plan main v3.5.7 条件8）。病院・支援・親の案は、ここでは触らない。
"""

from collections.abc import Iterable

from .ledger import Branch, Part, Profile

# 画面の語と台帳の相手。並びは画面の並び。
CONTACT_PARTS: dict[str, Part] = {
    "夫": Part.FAMILY,
    "保育園": Part.CHILDCARE,
    "職場": Part.WORK,
}


def contacts_from_choices(names: Iterable[str] | None) -> list[Part] | None:
    """選んだ語を台帳の相手にする。選んでいなければ None（誰も外さない）。"""
    if names is None:
        return None
    picked = set(names)
    return [part for name, part in CONTACT_PARTS.items() if name in picked]


def drop_unchecked_contacts(branch: Branch, profile: Profile) -> Branch:
    """チェックを外した相手への案を落とした分かれ道を返す。元の分かれ道は変えない。"""
    if profile.contacts is None:
        return branch
    unchecked = set(CONTACT_PARTS.values()) - set(profile.contacts)
    if not unchecked:
        return branch
    return branch.model_copy(
        update={
            "outcomes": [
                outcome.model_copy(
                    update={
                        "actions": [
                            action for action in outcome.actions if action.part not in unchecked
                        ]
                    }
                )
                for outcome in branch.outcomes
            ]
        }
    )
