.PHONY: hassfest hacs

hassfest:
	docker run --rm -v "$(CURDIR):/github/workspace" ghcr.io/home-assistant/hassfest

hacs:
	@validation_token="$$(gh auth token)"; \
	repository="$$(gh repo view --json nameWithOwner --jq .nameWithOwner)"; \
	ref="$$(git branch --show-current)"; \
	echo "HACS validates the remote GitHub ref $$repository@$$ref; unpushed changes are not visible."; \
	docker run --rm -v "$(CURDIR):/github/workspace" \
		-e GITHUB_WORKSPACE=/github/workspace \
		-e INPUT_GITHUB_TOKEN="$$validation_token" \
		-e INPUT_CATEGORY=integration \
		-e INPUT_REPOSITORY="$$repository" \
		-e INPUT_COMMENT=false \
		-e REPOSITORY_REF="$$ref" \
		ghcr.io/hacs/action:main
