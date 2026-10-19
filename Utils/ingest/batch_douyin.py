#!/usr/bin/env python3
"""
批量抓取抖音视频内容

用法:
    python batch_douyin.py urls.txt

urls.txt 格式（每行一个URL）:
    https://v.douyin.com/xxx/
    https://v.douyin.com/yyy/
"""
import sys
from pathlib import Path
from sources.web import fetch

def main():
    if len(sys.argv) < 2:
        print("用法: python batch_douyin.py urls.txt")
        sys.exit(1)

    urls_file = Path(sys.argv[1])
    if not urls_file.exists():
        print(f"错误: 文件不存在 {urls_file}")
        sys.exit(1)

    urls = []
    with open(urls_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                urls.append(line)

    print(f"找到 {len(urls)} 个URL，开始抓取...\n")

    success = 0
    failed = 0

    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] 抓取: {url}")
        try:
            output = fetch(url)
            print(f"  ✓ 保存到: {output}")
            success += 1
        except Exception as e:
            print(f"  ✗ 失败: {e}")
            failed += 1
        print()

    print(f"\n完成！成功: {success}, 失败: {failed}")

if __name__ == "__main__":
    main()
