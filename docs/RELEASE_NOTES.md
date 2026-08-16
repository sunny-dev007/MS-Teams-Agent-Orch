# Release Notes for PR 41

## Summary

PR 41 introduces several enhancements and bug fixes to the WhatsApp AI Agent platform. This release focuses on improving the reliability and performance of the agent workflows, as well as adding new features to enhance user experience.

## New Features

- **Enhanced Email Agent:** The Email Agent now supports automatic classification of emails into actionable items, improving the efficiency of email processing.
- **Improved Calendar Integration:** The Calendar Agent can now create Google Meet links automatically when scheduling meetings, streamlining the meeting setup process.
- **Repository Browsing:** Users can now browse repositories more efficiently with improved filtering and sorting options.

## Bug Fixes

- **WhatsApp Message Parsing:** Fixed an issue where certain WhatsApp messages were not being parsed correctly, leading to missed actions.
- **OAuth Token Refresh:** Resolved a bug where OAuth tokens were not refreshing correctly, causing authentication failures.

## Breaking Changes

- **API Endpoint Update:** The `/api/channels/copilot/message` endpoint now requires an additional header for authentication. Ensure that all integrations are updated to include this header.

## Known Issues

- **Delayed Notifications:** Some users may experience delays in receiving notifications due to network latency. We are actively working on optimizing this.

## Upgrade Instructions

1. Update your API integrations to include the new authentication header.
2. Review the new features and adjust your workflows accordingly.
3. Monitor the system for any unexpected behavior and report issues to the development team.

## Acknowledgments

Thanks to all contributors who helped in identifying issues and providing valuable feedback for this release.
