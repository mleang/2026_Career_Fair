import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer, CircleModuleDrawer, SquareModuleDrawer
from PIL import Image
import streamlit as st
import argparse
import sys
from pathlib import Path


class SimpleQRCode:
    """Generate simple black & white QR codes with dual logo support and module drawer styles."""
    
    def __init__(self, url, error_correction='H', module_drawer='square'):
        """Initialize QR code generator."""
        self.url = url
        self.error_correction = getattr(qrcode.constants, f'ERROR_CORRECT_{error_correction}')
        self.module_drawer = module_drawer
    
    def _get_drawer(self):
        """Get the appropriate module drawer."""
        if self.module_drawer == 'rounded':
            return RoundedModuleDrawer()
        elif self.module_drawer == 'circle':
            return CircleModuleDrawer()
        else:  # square
            return SquareModuleDrawer()
    
    @staticmethod
    def _prepare_logo(logo_path, size_percent, qr_size, angle=0, padding=10):
        """Prepare logo with proper transparency handling."""
        try:
            logo = Image.open(logo_path)
            
            if logo.mode != 'RGBA':
                logo = logo.convert('RGBA')
            
            # Resize logo based on percentage
            logo_size = int(qr_size * (size_percent / 100))
            logo.thumbnail((logo_size, logo_size), Image.Resampling.LANCZOS)
            
            # Rotate if angle is provided
            if angle != 0:
                logo = logo.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
            
            # Create white background for logo
            logo_bg = Image.new('RGB',
                               (logo.size[0] + padding * 2,
                                logo.size[1] + padding * 2),
                               'white')
            
            # Paste logo onto white background using alpha_composite
            logo_bg_rgba = logo_bg.convert('RGBA')
            logo_offset = (padding, padding)
            
            # Create a temporary image to composite
            temp = Image.new('RGBA', logo_bg_rgba.size, (255, 255, 255, 255))
            temp.paste(logo, logo_offset, logo)
            
            # Convert back to RGB
            logo_bg = Image.alpha_composite(logo_bg_rgba, temp).convert('RGB')
            
            return logo_bg
            
        except Exception as e:
            print(f"      ⚠️  Error preparing logo: {e}")
            return None
    
    def create_dual_centered_logos(self, filename, sponsor_name,
                                  sponsor_logo_path=None, hi_paris_logo_path=None,
                                  sponsor_logo_size=20, hi_paris_logo_size=15):
        """Create black & white QR code with Hi-PARIS on left and sponsor on right (both centered vertically)."""
        
        # Generate styled QR code with module drawer (pure black modules)
        drawer = self._get_drawer()
        
        qr = qrcode.QRCode(
            version=1,
            error_correction=self.error_correction,
            box_size=10,
            border=2,
            image_factory=StyledPilImage,
        )
        qr.add_data(self.url)
        qr.make(fit=True)
        
        # Create image with pure black modules
        qr_img = qr.make_image(
            fill_color="black",      # Pure black for modules
            back_color="white",      # White background
            module_drawer=drawer
        ).convert('RGB')
        
        qr_size = qr_img.size[0]
        
        logos_to_place = []
        
        # Prepare Hi-PARIS logo (left side)
        if hi_paris_logo_path and Path(hi_paris_logo_path).exists():
            hp_logo = self._prepare_logo(
                hi_paris_logo_path,
                hi_paris_logo_size,
                qr_size,
                angle=0,
                padding=8,
            )
            if hp_logo:
                logos_to_place.append(('hp', hp_logo))
        
        # Prepare sponsor logo (right side)
        if sponsor_logo_path and Path(sponsor_logo_path).exists():
            sp_logo = self._prepare_logo(
                sponsor_logo_path,
                sponsor_logo_size,
                qr_size,
                angle=0,
                padding=10,
            )
            if sp_logo:
                logos_to_place.append(('sponsor', sp_logo))
        
        # Place logos
        margin = 15
        for logo_type, logo_bg in logos_to_place:
            if logo_type == 'hp':
                # Left center
                logo_x = margin
                logo_y = (qr_size - logo_bg.size[1]) // 2
            else:  # sponsor
                # Right center
                logo_x = qr_size - logo_bg.size[0] - margin
                logo_y = (qr_size - logo_bg.size[1]) // 2
            
            try:
                qr_img.paste(logo_bg, (logo_x, logo_y))
            except Exception as e:
                print(f"      ⚠️  Could not paste {logo_type} logo: {e}")
        
        qr_img.save(filename, quality=95)
    

def list_doodle_urls():
    """Load Doodle URLs from secrets.toml"""
    urls = {
        "capgemini": st.secrets.get("DOODLE_URL_TO_SHARE_CAPGEMINI"),  
        "loreal": st.secrets.get("DOODLE_URL_TO_SHARE_LOREAL"),
        "schneider": st.secrets.get("DOODLE_URL_TO_SHARE_SCHNEIDER"),
        "total": st.secrets.get("DOODLE_URL_TO_SHARE_TOTAL"),
        "vinci": st.secrets.get("DOODLE_URL_TO_SHARE_VINCI")
    }
    return urls


def get_sponsor_logo_path(sponsor_name, logo_map):
    """Get sponsor logo path based on sponsor name."""
    return logo_map.get(sponsor_name.lower())


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Generate simple black & white QR codes with dual logos and module drawer styles."
    )
    
    # Module drawer argument
    parser.add_argument(
        "-drawer", "--drawer",
        type=str,
        choices=['rounded', 'circle', 'square'],
        default='square',
        help="Module drawer style (default: square)"
    )
    
    # Hi-PARIS logo arguments
    parser.add_argument(
        "-hp", "--hi-paris",
        type=str,
        default="images/hi-paris.png",
        help="Path to Hi-PARIS logo"
    )
    parser.add_argument(
        "--hi-paris-size",
        type=int,
        default=15,
        help="Hi-PARIS logo size as percentage of QR code (default: 15%)"
    )
    
    # Sponsor logo arguments
    parser.add_argument(
        "--sponsor-size",
        type=int,
        default=20,
        help="Sponsor logo size as percentage of QR code (default: 20%)"
    )

    # Sponsor logo paths
    parser.add_argument(
        "--loreal",
        type=str,
        default="images/logo-partenaires-loreal-300x135.png",
        help="Path to L'Oreal logo"
    )
    parser.add_argument(
        "--capgemini",
        type=str,
        default="images/logos-partenaires-capgemini-300x135.png",
        help="Path to Capgemini logo"
    )
    parser.add_argument(
        "--schneider",
        type=str,
        default="images/Logo-partenaires-SchneiderElectric_.png",
        help="Path to Schneider logo"
    )
    parser.add_argument(
        "--total",
        type=str,
        default="images/logo-partenaires-total-energie-300x135.png",
        help="Path to Total logo"
    )
    parser.add_argument(
        "--vinci",
        type=str,
        default="images/logo-partenaires-vinci.png",
        help="Path to Vinci logo"
    )
    

    args = parser.parse_args()

    # Create output directory with drawer type
    output_dir = Path(f"qr_codes_simple_dual_centered_{args.drawer}")
    output_dir.mkdir(exist_ok=True)

    doodle_urls = list_doodle_urls()

    # Check for missing URLs
    missing_urls = {k: v for k, v in doodle_urls.items() if v is None}
    if missing_urls:
        print("❌ ERROR: Missing Doodle URLs in secrets.toml")
        sys.exit(1)

    # Create sponsor logo map
    sponsor_logos = {
        'loreal': args.loreal,
        'capgemini': args.capgemini,
        'schneider': args.schneider,
        'total': args.total,
        'vinci': args.vinci
    }
    
    # Validate sponsor logos
    print("✅ Sponsor Logos:")
    for sponsor, logo_path in sponsor_logos.items():
        logo_file = Path(logo_path)
        if not logo_file.exists():
            print(f"⚠️  {sponsor.upper()}: Not found (will skip logo)")
        else:
            print(f"   📷 {sponsor.upper()}: {logo_file.name}")
    
    print()
    
    # Validate Hi-PARIS logo
    if args.hi_paris:
        hp_path = Path(args.hi_paris)
        if not hp_path.exists():
            print(f"⚠️  Hi-PARIS logo not found: {args.hi_paris}")
            args.hi_paris = None
        else:
            print(f"✅ Hi-PARIS Logo: {hp_path.name}\n")

    # Print configuration
    print("🎨 Configuration:")
    print(f"   QR Code: Black & White (NO COLORS)")
    print(f"   Module Drawer: {args.drawer.upper()}")
    
    
    print(f"\n   Module Drawer:")
    if args.drawer == 'rounded':
        print(f"   • RoundedModuleDrawer: Smooth rounded corners")
    elif args.drawer == 'circle':
        print(f"   • CircleModuleDrawer: Modern circle modules")
    else:
        print(f"   • SquareModuleDrawer: Classic square modules (standard)")
    
    print(f"\n   Logo Sizing:")
    print(f"   • Sponsor size: {args.sponsor_size}%")
    print(f"   • Hi-PARIS size: {args.hi_paris_size}%")
    print()

    # Generate QR codes
    print(f"🎨 Generating simple QR codes ...", end="")
   
    success_count = 0
    
    for sponsor_key, url in doodle_urls.items():
        try:
            sponsor_name = sponsor_key.upper()
            print(f"📄 {sponsor_name:12}", end=" → ")
            
            qr_gen = SimpleQRCode(url, error_correction='H', module_drawer=args.drawer)
            
            # Create filename with layout and drawer
            filename_parts = [
                sponsor_name,
                'qr',
                'simple',
                "dual_centered",
                args.drawer
            ]
            filename = '_'.join(filename_parts) + '.png'
            output_path = output_dir / filename
            
            # Get sponsor-specific logo
            sponsor_logo = get_sponsor_logo_path(sponsor_key, sponsor_logos)
           
            qr_gen.create_dual_centered_logos(
                str(output_path),
                sponsor_key,
                sponsor_logo_path=sponsor_logo,
                hi_paris_logo_path=args.hi_paris,
                sponsor_logo_size=args.sponsor_size,
                hi_paris_logo_size=args.hi_paris_size
            )
            print("✅")

            success_count += 1
            
        except Exception as e:
            print(f"❌ Failed: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*80}")
    print(f"✅ Generated {success_count}/{len(doodle_urls)} simple QR codes")
    print(f"📁 Output: {output_dir.absolute()}")
    print(f"{'='*80}\n")