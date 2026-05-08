"""
현재 프롬프트(v1.0)를 prompt_versions 테이블에 seeding.
실행: python -m scripts.seed_prompt_versions
"""
import sys
sys.path.insert(0, ".")

from app.core.database import SessionLocal
from app.models.recommendation import PromptVersion
from app.services.gemini.prompts import STAGE1_MACRO, STAGE2_HISTORICAL, STAGE3_INDUSTRY, STAGE4_PICKS

SEEDS = [
    (1, "v1.0", STAGE1_MACRO),
    (2, "v1.0", STAGE2_HISTORICAL),
    (3, "v1.0", STAGE3_INDUSTRY),
    (4, "v1.0", STAGE4_PICKS),
]

def main():
    db = SessionLocal()
    created = 0
    for stage, version_no, prompt_text in SEEDS:
        exists = db.query(PromptVersion).filter(
            PromptVersion.stage == stage,
            PromptVersion.version_no == version_no,
        ).first()
        if exists:
            print(f"  skip stage={stage} {version_no} (already exists)")
            continue
        db.add(PromptVersion(stage=stage, version_no=version_no, prompt_text=prompt_text))
        created += 1
        print(f"  created stage={stage} {version_no}")
    db.commit()
    db.close()
    print(f"\n완료: {created}개 생성")

if __name__ == "__main__":
    main()
