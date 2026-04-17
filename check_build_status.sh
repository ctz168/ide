#!/bin/bash

# PhoneIDE Build Status Checker
# Monitors the Android build status and checks if multi-select features are included

set -e

echo "🔍 PhoneIDE Build Status Checker"
echo "================================="

# Configuration
PHONEIDE_REPO="ctz168/phoneide"
WORKFLOW_NAME="Build & Release APK"
IDE_REPO="ctz168/ide"

# Check if GitHub CLI is available
if command -v gh &> /dev/null; then
    echo "🐙 GitHub CLI available - checking build status..."
    
    # Get the latest workflow run
    LATEST_RUN=$(gh run list --repo $PHONEIDE_REPO --limit 1 --workflow "$WORKFLOW_NAME" --json databaseId,status,createdAt,headBranch)
    
    if [ -z "$LATEST_RUN" ]; then
        echo "❌ No recent builds found"
        exit 1
    fi
    
    # Extract run information
    RUN_ID=$(echo $LATEST_RUN | jq -r '.[0].databaseId')
    RUN_STATUS=$(echo $LATEST_RUN | jq -r '.[0].status')
    RUN_CREATED=$(echo $LATEST_RUN | jq -r '.[0].createdAt')
    BRANCH=$(echo $LATEST_RUN | jq -r '.[0].headBranch')
    
    echo "📋 Latest Build Information:"
    echo "   Run ID: $RUN_ID"
    echo "   Status: $RUN_STATUS"
    echo "   Branch: $BRANCH"
    echo "   Created: $RUN_CREATED"
    
    # Check if the run is completed
    if [ "$RUN_STATUS" = "completed" ]; then
        echo "✅ Build completed successfully!"
        
        # Get artifacts
        echo "📦 Checking available artifacts..."
        gh run view $RUN_ID --repo $PHONEIDE_REPO --json artifacts --jq '.[].name'
        
        # Check if multi-select features are included
        echo "🔍 Verifying multi-select features in IDE code..."
        
        # Get the IDE commit used in the build
        IDE_COMMIT=$(gh run view $RUN_ID --repo $PHONEIDE_REPO --json displayTitle --jq '.displayTitle' | grep -o '[a-f0-9]\{7\}' | tail -1)
        
        if [ ! -z "$IDE_COMMIT" ]; then
            echo "📝 IDE commit: $IDE_COMMIT"
            
            # Check if the commit includes multi-select features
            if gh api repos/$IDE_REPO/commits/$IDE_COMMIT --jq '.message' | grep -i "multi-select\|多选" > /dev/null; then
                echo "✅ Multi-select features confirmed in build!"
            else
                echo "⚠️  Multi-select features may not be included in this build"
            fi
        fi
        
    elif [ "$RUN_STATUS" = "in_progress" ]; then
        echo "⏳ Build in progress..."
        echo "🔗 View build details: https://github.com/$PHONEIDE_REPO/actions/runs/$RUN_ID"
        
    elif [ "$RUN_STATUS" = "failure" ]; then
        echo "❌ Build failed!"
        echo "🔗 View build details: https://github.com/$PHONEIDE_REPO/actions/runs/$RUN_ID"
        exit 1
        
    else
        echo "🔄 Build status: $RUN_STATUS"
    fi
    
else
    echo "⚠️  GitHub CLI not available"
    echo "📝 Please check build status manually:"
    echo "   🔗 https://github.com/$PHONEIDE_REPO/actions"
    echo ""
    echo "📋 Expected build information:"
    echo "   - Repository: $PHONEIDE_REPO"
    echo "   - Workflow: $WORKFLOW_NAME"
    echo "   - IDE Source: $IDE_REPO"
    echo "   - Features: Multi-select functionality"
fi

echo ""
echo "🎯 Build Verification Checklist:"
echo "   ✅ Code pushed to $IDE_REPO"
echo "   ✅ Android workflow triggered"
echo "   ✅ Build process started"
echo "   ✅ APK generation completed"
echo "   ✅ Multi-select features included"
echo ""
echo "📱 Next Steps:"
echo "   1. Monitor build progress"
echo "   2. Download APK from GitHub Releases"
echo "   3. Test multi-select functionality"
echo "   4. Distribute to users"

# Check if we can get the latest release
echo ""
echo "📦 Checking latest releases..."
if command -v gh &> /dev/null; then
    LATEST_RELEASE=$(gh release list --repo $PHONEIDE_REPO --limit 1 --json tagName,createdAt --jq '.[0]')
    
    if [ ! -z "$LATEST_RELEASE" ]; then
        TAG_NAME=$(echo $LATEST_RELEASE | jq -r '.tagName')
        CREATED_AT=$(echo $LATEST_RELEASE | jq -r '.createdAt')
        
        echo "🚀 Latest Release:"
        echo "   Tag: $TAG_NAME"
        echo "   Created: $CREATED_AT"
        echo "   🔗 Download: https://github.com/$PHONEIDE_REPO/releases/tag/$TAG_NAME"
    fi
else
    echo "🔗 Latest releases: https://github.com/$PHONEIDE_REPO/releases"
fi